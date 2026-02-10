"""
Gerenciador de token JWT do SuperProfessor.
Armazena, carrega e renova o token automaticamente.
"""

import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from loguru import logger


class TokenManager:
    """Gerencia o token JWT para acesso à API do SuperProfessor."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.token_file = storage_dir / "jwt_token.json"
        self.browser_state_file = storage_dir / "browser_state.json"
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: datetime | None = None
        self._load_token()

    def _load_token(self):
        """Carrega token salvo do disco."""
        if not self.token_file.exists():
            logger.info("Nenhum token JWT salvo encontrado")
            return

        try:
            data = json.loads(self.token_file.read_text(encoding="utf-8"))
            self._access_token = data.get("accessToken")
            self._refresh_token = data.get("refreshToken")
            expires = data.get("accessTokenExpiresAt")
            if expires:
                self._expires_at = datetime.fromisoformat(
                    expires.replace("Z", "+00:00")
                )
            logger.info(f"Token JWT carregado (expira em {self._expires_at})")
        except Exception as e:
            logger.warning(f"Erro ao carregar token: {e}")

    def save_token(self, token_data: dict):
        """Salva token no disco."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(
            json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._access_token = token_data.get("accessToken")
        self._refresh_token = token_data.get("refreshToken")
        expires = token_data.get("accessTokenExpiresAt")
        if expires:
            self._expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        logger.info(f"Token JWT salvo (expira em {self._expires_at})")

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @property
    def is_valid(self) -> bool:
        """Verifica se o token ainda é válido."""
        if not self._access_token or not self._expires_at:
            return False
        now = datetime.now(timezone.utc)
        # Margem de 1 hora antes da expiração
        return now < self._expires_at

    @property
    def headers(self) -> dict:
        """Headers HTTP com autenticação para a API do SuperProfessor."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://interno.superprofessor.com.br",
            "Referer": "https://interno.superprofessor.com.br/",
        }

    async def ensure_valid_token(self) -> str:
        """
        Garante que temos um token válido.
        Se expirado, faz login via Playwright para obter novo.
        """
        if self.is_valid:
            return self._access_token

        logger.warning(
            "Token JWT expirado ou inexistente. Iniciando login via browser..."
        )
        await self._login_via_browser()

        if not self.is_valid:
            raise RuntimeError("Falha ao obter token JWT válido")

        return self._access_token

    async def _login_via_browser(self):
        """Faz login no SuperProfessor via Playwright e captura o JWT."""
        from playwright.async_api import async_playwright
        from .config import settings

        email = settings.SUPERPRO_EMAIL
        password = settings.SUPERPRO_PASSWORD
        if not email or not password:
            logger.error(
                f"Credenciais não configuradas! SUPERPRO_EMAIL={'definido' if email else 'VAZIO'}, "
                f"SUPERPRO_PASSWORD={'definido' if password else 'VAZIO'}"
            )
            raise RuntimeError("SUPERPRO_EMAIL e/ou SUPERPRO_PASSWORD não definidos")

        logger.info(
            f"Credenciais: email={email[:3]}***@***, headless={settings.HEADLESS}"
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=settings.HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            # Tentar restaurar sessão existente
            if self.browser_state_file.exists():
                try:
                    context = await browser.new_context(
                        storage_state=str(self.browser_state_file)
                    )
                    logger.info("Sessão existente restaurada")
                except Exception:
                    context = await browser.new_context()
            else:
                context = await browser.new_context()

            page = await context.new_page()

            # Interceptar response do validate-token para capturar JWT
            captured_token = {}

            async def on_response(response):
                if "validate-token" in response.url and response.status == 200:
                    try:
                        body = await response.json()
                        if "accessToken" in body:
                            captured_token.update(body)
                            logger.info("Token JWT capturado via interceptação!")
                    except Exception:
                        pass

            page.on("response", on_response)

            # Navegar para a home (com sessão deve redirecionar logado)
            await page.goto(
                "https://interno.superprofessor.com.br", wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(3000)

            # Se não capturou token, fazer login
            if not captured_token:
                logger.info(f"Fazendo login em {settings.LOGIN_URL}...")
                await page.goto(settings.LOGIN_URL, wait_until="domcontentloaded")
                logger.info(f"Página carregada. URL atual: {page.url}")

                # Aguardar campo de email renderizar (SPA pode demorar)
                try:
                    await page.wait_for_selector(
                        'input[type="email"]', state="visible", timeout=60000
                    )
                    logger.info("Campo de email encontrado")
                except Exception:
                    # Screenshot para diagnóstico
                    screenshot_path = self.storage_dir / "login_error.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    page_title = await page.title()
                    logger.error(
                        f"Campo de email não encontrado após 60s. "
                        f"URL: {page.url} | Título: {page_title} | "
                        f"Screenshot salvo em: {screenshot_path}"
                    )
                    await browser.close()
                    raise RuntimeError("Página de login não carregou o formulário")

                # Preencher credenciais
                await page.fill('input[type="email"]', email)
                await page.fill('input[type="password"]', password)
                logger.info("Credenciais preenchidas, submetendo...")

                # Submeter
                await page.click('button[type="submit"]')
                await page.wait_for_timeout(5000)

                # Navegar para montar-prova para garantir que o token é emitido
                await page.goto(
                    "https://interno.superprofessor.com.br/montar-prova",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(3000)

            if captured_token:
                self.save_token(captured_token)

                # Salvar browser state
                state = await context.storage_state()
                self.browser_state_file.write_text(
                    json.dumps(state, indent=2), encoding="utf-8"
                )
                logger.info("Browser state salvo")
            else:
                logger.error("Não foi possível capturar o token JWT")

            await browser.close()
