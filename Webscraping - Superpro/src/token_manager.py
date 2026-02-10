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
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            # Contexto com fingerprint realista para evitar reCAPTCHA
            context_opts = {
                "viewport": {"width": 1366, "height": 768},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "locale": "pt-BR",
                "timezone_id": "America/Sao_Paulo",
            }

            # Tentar restaurar sessão existente
            if self.browser_state_file.exists():
                try:
                    context_opts["storage_state"] = str(self.browser_state_file)
                    context = await browser.new_context(**context_opts)
                    logger.info("Sessão existente restaurada")
                except Exception:
                    del context_opts["storage_state"]
                    context = await browser.new_context(**context_opts)
            else:
                context = await browser.new_context(**context_opts)

            page = await context.new_page()

            # Stealth: remover marcadores de automação
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
            """)

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

            # Se temos sessão salva, testar restauração
            if self.browser_state_file.exists():
                await page.goto(
                    "https://interno.superprofessor.com.br",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(5000)

            # Se não capturou token via sessão, fazer login
            if not captured_token:
                logger.info(f"Fazendo login em {settings.LOGIN_URL}...")
                await page.goto(
                    settings.LOGIN_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                logger.info(f"Página carregada. URL atual: {page.url}")

                # Aguardar campo de login renderizar (SPA pode demorar)
                try:
                    await page.wait_for_selector(
                        'input[name="login"]', state="visible", timeout=60000
                    )
                    logger.info("Campo de login encontrado")
                except Exception:
                    screenshot_path = self.storage_dir / "login_error.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    page_title = await page.title()
                    logger.error(
                        f"Campo de login não encontrado após 60s. "
                        f"URL: {page.url} | Título: {page_title} | "
                        f"Screenshot salvo em: {screenshot_path}"
                    )
                    await browser.close()
                    raise RuntimeError("Página de login não carregou o formulário")

                # Fechar banner de cookies se existir
                try:
                    accept_btn = page.locator(
                        'button:has-text("Aceitar"), button:has-text("Aceito")'
                    ).first
                    if await accept_btn.is_visible(timeout=3000):
                        await accept_btn.click()
                        logger.debug("Banner de cookies fechado")
                        await page.wait_for_timeout(500)
                except Exception:
                    pass

                # Preencher credenciais com comportamento humanizado
                import asyncio as _asyncio
                import random

                email_input = page.locator('input[name="login"]').first
                await email_input.click()
                await _asyncio.sleep(random.uniform(0.3, 0.8))
                await email_input.fill(email)

                await _asyncio.sleep(random.uniform(0.5, 1.0))

                password_input = page.locator('input[name="senha"]').first
                await password_input.click()
                await _asyncio.sleep(random.uniform(0.3, 0.8))
                await password_input.fill(password)

                logger.info("Credenciais preenchidas, submetendo...")
                await _asyncio.sleep(random.uniform(0.5, 1.0))

                # Submeter
                await page.click('button[type="submit"]')

                # Aguardar redirecionamento pós-login (URL sai do /acesso-professor)
                try:
                    await page.wait_for_url(
                        lambda url: "acesso-professor" not in url,
                        timeout=30000,
                    )
                    logger.info(f"Redirecionado para: {page.url}")
                except Exception:
                    # Screenshot para ver o que aconteceu
                    screenshot_path = self.storage_dir / "login_submit_error.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    logger.warning(
                        f"URL não mudou após submit. URL: {page.url} | "
                        f"Screenshot: {screenshot_path}"
                    )

                # Aguardar interceptação do token
                await page.wait_for_timeout(5000)

                # Se ainda não capturou, navegar para interno para forçar validate-token
                if not captured_token:
                    logger.info("Token não capturado, navegando para interno...")
                    await page.goto(
                        "https://interno.superprofessor.com.br",
                        wait_until="domcontentloaded",
                    )
                    await page.wait_for_timeout(5000)

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
