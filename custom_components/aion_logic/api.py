"""API Client voor de Aion Logic® Gateway (Async Version)."""
import aiohttp
import asyncio
import logging
from .const import AION_LOGIC_GATEWAY_URL, AION_LOGIC_VERIFY_URL

_LOGGER = logging.getLogger(__name__)

class ApiConnectionError(Exception): 
    """Fout bij het verbinden met de API.""" 
    pass

class ApiTimeoutError(Exception): 
    """Timeout bij het verbinden met de API.""" 
    pass

class ApiAuthError(Exception): 
    """Ongeldige of inactieve licentie.""" 
    pass

class AionLogicApiClient:
    """De API Client die communiceert met de Aion Logic Cloud via aiohttp."""

    def __init__(self, activation_code: str, session: aiohttp.ClientSession):
        self._activation_code = activation_code
        self._session = session
        self._gateway_url = AION_LOGIC_GATEWAY_URL.rstrip('/')
        self._verify_url = AION_LOGIC_VERIFY_URL
        self._headers = {
            "Content-Type": "application/json", 
            "Accept": "application/json"
        }

    async def activate_license(self) -> dict:
        """Activeert de licentie via de verify-server."""
        payload = {"license_key": self._activation_code}
        try:
            _LOGGER.debug(f"Licentie activeren via: {self._verify_url}")
            async with asyncio.timeout(15):
                async with self._session.post(self._verify_url, json=payload, headers=self._headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("valid") is True:
                            _LOGGER.info(f"Licentie succesvol geactiveerd: {data.get('message')}")
                            return {"valid": True, "message": data.get("message")}
                        else:
                            _LOGGER.warning(f"Licentie geweigerd: {data.get('message')}")
                            raise ApiAuthError(data.get("message", "Ongeldige licentie"))
                    else:
                        _LOGGER.error(f"Verificatie server fout: {response.status}")
                        raise ApiConnectionError(f"Server fout: {response.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            _LOGGER.error(f"Kan verificatie server niet bereiken: {e}")
            raise ApiConnectionError(f"Verbindingsfout: {e}")

    async def _make_request(self, endpoint: str, payload: dict, timeout: int = 35) -> dict:
        """Stuurt een async verzoek naar de Gateway met verbeterde error-trapping."""
        url = f"{self._gateway_url}{endpoint}"
        data_to_send = {"activation_code": self._activation_code, "payload": payload}

        try:
            # We gebruiken ClientTimeout om er zeker van te zijn dat aiohttp zelf ook afbreekt
            total_timeout = aiohttp.ClientTimeout(total=timeout)
            
            async with self._session.post(
                url, 
                json=data_to_send, 
                headers=self._headers, 
                timeout=total_timeout
            ) as response:
                
                if response.status == 200:
                    try:
                        return await response.json()
                    except Exception as json_err:
                        raise ApiConnectionError(f"Gateway gaf HTML in plaats van JSON: {json_err}")
                if response.status == 403:
                    _LOGGER.error("Licentie geweigerd door Gateway (403).")
                    raise ApiAuthError("Licentie ongeldig of verlopen.")
                
                raise ApiConnectionError(f"Gateway fout: {response.status}")
                
        except asyncio.TimeoutError:
            _LOGGER.error(f"⏰ Timeout na {timeout}s op endpoint {endpoint}")
            raise ApiTimeoutError("Timeout gateway.")
        except aiohttp.ClientError as e:
            _LOGGER.error(f"🔌 Netwerkfout bij verbinden met Gateway: {e}")
            raise ApiConnectionError(f"Verbindingsfout: {e}")

    async def validate_connection(self) -> str:
        """Checkt verbinding (Post-Installatie)."""
        try:
            await self._make_request("/main_logic", payload={"test_connection": True}, timeout=10)
            return "valid"
        except ApiAuthError: return "invalid_auth"
        except ApiTimeoutError: return "timeout"
        except Exception: return "cannot_connect"

    async def trigger_main_logic(self, payload: dict) -> dict:
        return await self._make_request("/main_logic", payload)

    async def trigger_proactive_start(self, payload: dict) -> dict:
        return await self._make_request("/proactive_start", payload)

    async def build_dashboard(self, payload: dict) -> dict:
        return await self._make_request("/build_dashboard", payload)
