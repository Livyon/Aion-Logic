"""Button platform voor Aion Logic™."""
import logging
import os
import shutil

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# --- Thema Constanten ---
THEME_DESTINATION_FILENAME = "aion_logic.yaml"
THEME_SOURCE_FILENAME = "aion_logic_theme.yaml" 
THEME_SOURCE_SUBDIR = "themes"

# --- Dashboard Constanten ---
DASHBOARD_DESTINATION_DIR = os.path.join("custom_components", DOMAIN, "www")
DASHBOARD_PUBLIC_FILENAME = "aion_logic-dashboard-code.txt"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Zet de Aion Logic buttons op."""
    entities =[]
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # 1. Dashboard Installer (SaaS Auto-Builder)
    entities.append(AionLogicInstallButton(
        hass, entry, "dashboard", "mdi:view-dashboard-variant-outline", "Installeer Dashboard Template"
    ))

    # 2. Theme Installer
    entities.append(AionLogicInstallButton(
        hass, entry, "theme", "mdi:palette-outline", "Installeer Aion Thema"
    ))
    
    # 3. Simulatie Knoppen
    entities.append(AionLogicSimulationButton(coordinator, entry, "fire", "Test: Brandalarm", "mdi:fire-alert"))
    entities.append(AionLogicSimulationButton(coordinator, entry, "intrusion", "Test: Inbraakalarm", "mdi:shield-alert"))
    entities.append(AionLogicSimulationButton(coordinator, entry, "arrival", "Test: Thuiskomst (Auto)", "mdi:car-connected"))

    async_add_entities(entities)

def _save_dashboard_file(hass: HomeAssistant, dashboard_yaml: str, room_count: int) -> tuple[bool, str]:
    """Slaat de door de Cloud gegenereerde YAML op."""
    try:
        # Definieer hier opnieuw de paden
        dest_dir = os.path.join(hass.config.path(), "custom_components", DOMAIN, "www")
        dest_path = os.path.join(dest_dir, DASHBOARD_PUBLIC_FILENAME)
        
        file_exists = os.path.exists(dest_path)
            
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
            
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(dashboard_yaml)
            
        link_url = f"/{DOMAIN}_assets/{DASHBOARD_PUBLIC_FILENAME}"
        
        if file_exists:
            return True, f"⚠️ Waarschuwing: Bestaande dashboard template overschreven.\n\nNieuwe code gegenereerd voor {room_count} kamers.\n\n👉[**RECHTERMUISKNOP HIER -> OPEN IN NIEUW TABBLAD**]({link_url})\n\n*(Op mobiel: houd de link lang ingedrukt)*"
        return True, f"Het dashboard is op maat gegenereerd voor {room_count} kamers.\n\n👉[**RECHTERMUISKNOP HIER -> OPEN IN NIEUW TABBLAD**]({link_url})\n\n*(Op mobiel: houd de link lang ingedrukt)*"        
        
    except Exception as e:
        _LOGGER.error(f"Fout bij opslaan dashboard: {e}")
        return False, f"Onverwachte fout: {e}"

def _copy_file_to_config(hass: HomeAssistant, source_dir: str, source_file: str, dest_dir_name: str, dest_file_name: str) -> tuple[bool, str]:
    """Legacy kopieerfunctie (wordt nog gebruikt voor Thema)."""
    try:
        integration_dir = os.path.dirname(__file__)
        source_path = os.path.join(integration_dir, source_dir, source_file)
        dest_dir = os.path.join(hass.config.path(), dest_dir_name)
        dest_path = os.path.join(dest_dir, dest_file_name)

        if not os.path.exists(source_path):
            return False, f"Bronbestand niet gevonden: {source_file}"
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        if os.path.exists(dest_path):
            return False, f"Veiligheid: Het bestand '{dest_file_name}' bestaat al. Hernoem of verwijder uw huidige bestand eerst."

        shutil.copy2(source_path, dest_path)
        return True, "Succesvol geïnstalleerd."

    except Exception as e:
        return False, f"Onverwachte fout: {e}"

class AionLogicInstallButton(ButtonEntity):
    """Knop om bestanden te installeren."""

    def __init__(self, hass, entry, blueprint_type, icon, name):
        self.hass = hass
        self._entry = entry
        self._blueprint_type = blueprint_type
        self._attr_unique_id = f"{entry.entry_id}_install_{blueprint_type}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Aion Logic")
        
        if blueprint_type == "theme":
            self._source_file = THEME_SOURCE_FILENAME
            self._dest_file = THEME_DESTINATION_FILENAME
            self._dest_dir = THEME_SOURCE_SUBDIR
            self._title = "Aion Logic Thema"
        elif blueprint_type == "dashboard":
            self._title = "Dashboard Template"

    async def async_press(self) -> None:
        if self._blueprint_type == "dashboard":
            # HAAL LIVE DE NAMEN OP (SaaS Auto-Sync)
            from homeassistant.helpers import area_registry as ar
            area_reg = ar.async_get(self.hass)
            area_names = {area_id: area.name for area_id, area in area_reg.areas.items()}
            
            # Vraag het Dashboard op aan de Cloud
            coordinator = self.hass.data[DOMAIN][self._entry.entry_id]
            try:
                payload = {"area_names": area_names, "options": dict(self._entry.options)}
                response = await coordinator.api_client.build_dashboard(payload)
                
                dashboard_yaml = response.get("dashboard_yaml")
                room_count = response.get("room_count", 0)
                
                if not dashboard_yaml: raise ValueError("Geen geldige YAML ontvangen van Aion Logic Cloud.")
                
                success, message = await self.hass.async_add_executor_job(
                    _save_dashboard_file, self.hass, dashboard_yaml, room_count
                )
            except Exception as e:
                success, message = False, f"Cloud Bouwfout: {e}"
        else:
            # Standaard Thema kopiëren
            success, message = await self.hass.async_add_executor_job(
                _copy_file_to_config, self.hass, THEME_SOURCE_SUBDIR, self._source_file, self._dest_dir, self._dest_file
            )

        if success:
            if self._blueprint_type == "theme":
                await self.hass.services.async_call("frontend", "reload_themes")
            await self.hass.services.async_call(
                "persistent_notification", "create", {
                    "title": f"Aion Logic: {self._title} Geïnstalleerd",
                    "message": f"Succes! {message}\nIndien nodig, herstart Home Assistant of ververs uw browser.",
                    "notification_id": f"aion_logic_install_{self._blueprint_type}",
                })
        else:
            await self.hass.services.async_call(
                "persistent_notification", "create", {
                    "title": f"Aion Logic: Installatie Gestopt",
                    "message": message,
                    "notification_id": f"aion_logic_install_error",
                })

class AionLogicSimulationButton(ButtonEntity):
    """Knop om simulaties te starten (Cloud Test)."""
    def __init__(self, coordinator, entry, sim_type, name, icon):
        self._coordinator = coordinator
        self._entry = entry
        self._sim_type = sim_type
        self._attr_unique_id = f"{entry.entry_id}_sim_{sim_type}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Aion Logic")

    async def async_press(self) -> None:
        await self._coordinator.async_run_simulation(self._sim_type)
