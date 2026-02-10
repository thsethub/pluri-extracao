"""
Gerenciador de sessão do navegador Playwright.
Cuida de: inicializar browser, fazer login, persistir cookies,
restaurar sessão, e controle anti-detecção.
"""

import asyncio
import json
import random
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from src.config import settings
from src.logger import log


class BrowserManager:
    """Gerencia o ciclo de vida do browser e sessão autenticada."""

    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._logged_in: bool = False

    @property
    def page(self) -> Page:
        """Página ativa."""
        if not self._page:
            raise RuntimeError("Browser não inicializado. Chame start() primeiro.")
        return self._page

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    async def start(self):
        """Inicia o Playwright + Chromium com configurações anti-detecção."""
        log.info("Iniciando navegador...")

        self._pw = await async_playwright().start()

        # Configurações para parecer um browser real
        self._browser = await self._pw.chromium.launch(
            headless=settings.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        # Tenta restaurar sessão salva
        storage_state = None
        if settings.STATE_FILE.exists():
            try:
                storage_state = str(settings.STATE_FILE)
                log.info("Restaurando sessão salva...")
            except Exception:
                storage_state = None

        self._context = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            storage_state=storage_state,
        )

        # Timeout padrão para todas as ações
        self._context.set_default_timeout(settings.NAVIGATION_TIMEOUT)

        self._page = await self._context.new_page()

        # Stealth: remover marcadores de automação
        await self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
        """)

        # Intercepta e bloqueia recursos pesados (imagens, fontes, analytics)
        await self._page.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot}",
            lambda route: route.abort(),
        )
        # Bloqueia trackers/analytics
        await self._page.route(
            "**/*google-analytics*/**",
            lambda route: route.abort(),
        )
        await self._page.route(
            "**/*goadopt*/**",
            lambda route: route.abort(),
        )

        log.success("Navegador iniciado")

    async def login(self) -> bool:
        """
        Realiza login no Super Professor.
        Se já estiver logado (sessão restaurada), verifica e pula o login.

        Returns:
            True se login ok, False se falhou.
        """
        try:
            # Verifica se a sessão restaurada ainda é válida
            if await self._check_session_valid():
                log.success("Sessão restaurada ainda é válida — login ignorado")
                self._logged_in = True
                return True

            log.info(f"Navegando para login: {settings.LOGIN_URL}")
            await self._page.goto(
                settings.LOGIN_URL, wait_until="domcontentloaded", timeout=60000
            )

            # Aguarda o formulário de login carregar (SPA pode demorar)
            await self._page.wait_for_selector(
                'input[name="login"]',
                state="visible",
                timeout=60000,
            )

            # Fecha cookie banner se existir
            await self._dismiss_cookie_banner()

            # Preenche o email
            email_input = self._page.locator('input[name="login"]').first
            await email_input.click()
            await asyncio.sleep(0.3)
            await email_input.fill(settings.SUPERPRO_EMAIL)
            log.debug("Email preenchido")

            # Delay humanizado
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # Preenche a senha
            password_input = self._page.locator('input[name="senha"]').first
            await password_input.click()
            await asyncio.sleep(0.3)
            await password_input.fill(settings.SUPERPRO_PASSWORD)
            log.debug("Senha preenchida")

            # Delay humanizado antes de clicar
            await asyncio.sleep(random.uniform(0.5, 1.0))

            # Clica no botão de entrar
            login_btn = self._page.locator(
                'button:has-text("Entrar"), button[type="submit"]'
            ).first
            await login_btn.click()
            log.info("Botão de login clicado, aguardando redirecionamento...")

            # Aguarda sair da página de login (URL muda)
            try:
                await self._page.wait_for_url(
                    lambda url: "/acesso-professor" not in url,
                    timeout=30000,
                )
            except Exception:
                # Mesmo que não redirecione, pode ter logado (SPA)
                log.debug("URL não mudou, verificando se logou via DOM...")

            # Aguarda a página carregar
            await self._page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(settings.DELAY_AFTER_LOGIN)

            self._logged_in = True
            log.success(f"Login realizado com sucesso! URL atual: {self._page.url}")

            # Salva o estado da sessão para reutilizar depois
            await self._save_session()

            return True

        except Exception as e:
            log.error(f"Falha no login: {e}")
            # Screenshot para debug
            try:
                screenshot_path = settings.LOG_DIR / "login_error.png"
                await self._page.screenshot(path=str(screenshot_path))
                log.info(f"Screenshot de erro salvo em {screenshot_path}")
            except Exception:
                pass
            return False

    async def _check_session_valid(self) -> bool:
        """Verifica se a sessão salva ainda está autenticada."""
        try:
            if not settings.STATE_FILE.exists():
                return False

            # Navega para uma página protegida para testar
            await self._page.goto(
                f"{settings.SUPERPRO_BASE_URL}/banco-de-questoes",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(2)

            current_url = self._page.url
            # Se redirecionou para login, a sessão expirou
            if "acesso-professor" in current_url or "login" in current_url:
                log.info("Sessão expirada, precisa refazer login")
                return False

            return True

        except Exception as e:
            log.debug(f"Erro ao verificar sessão: {e}")
            return False

    async def _dismiss_cookie_banner(self):
        """Fecha o banner de cookies se existir."""
        try:
            accept_btn = self._page.locator(
                'button:has-text("Aceitar"), button:has-text("Aceito"), '
                'button:has-text("Accept"), a:has-text("Aceitar")'
            ).first
            if await accept_btn.is_visible(timeout=3000):
                await accept_btn.click()
                log.debug("Banner de cookies fechado")
                await asyncio.sleep(0.5)
        except Exception:
            pass  # Sem banner, OK

    async def _save_session(self):
        """Salva estado do browser (cookies + localStorage) para persistência."""
        try:
            state = await self._context.storage_state(path=str(settings.STATE_FILE))
            log.debug(f"Sessão salva em {settings.STATE_FILE}")
        except Exception as e:
            log.warning(f"Erro ao salvar sessão: {e}")

    async def navigate(self, url: str, wait_until: str = "networkidle"):
        """Navega para uma URL com tratamento de erro."""
        try:
            await self._page.goto(url, wait_until=wait_until)
            await asyncio.sleep(random.uniform(1.0, 2.0))
        except Exception as e:
            log.error(f"Erro ao navegar para {url}: {e}")
            raise

    async def close(self):
        """Fecha browser e limpa recursos."""
        try:
            if self._logged_in:
                await self._save_session()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
            log.info("Navegador encerrado")
        except Exception as e:
            log.warning(f"Erro ao fechar navegador: {e}")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()
