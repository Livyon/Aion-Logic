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
DASHBOARD_TEMPLATE_FILENAME = "dashboard_template.yaml"
DASHBOARD_SOURCE_SUBDIR = "assets"
DASHBOARD_DESTINATION_DIR = os.path.join("custom_components", DOMAIN, "www")
DASHBOARD_PUBLIC_FILENAME = "aion_logic-dashboard-template.yaml"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Zet de Aion Logic buttons op."""
    entities = []
    
    # Haal coordinator op voor de simulatie-acties
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # 1. Dashboard Installer
    entities.append(AionLogicInstallButton(
        hass, 
        entry, 
        "dashboard", 
        "mdi:view-dashboard-variant-outline", 
        "Installeer Dashboard Template"
    ))

    # 2. Theme Installer
    entities.append(AionLogicInstallButton(
        hass, 
        entry, 
        "theme", 
        "mdi:palette-outline", 
        "Installeer Aion Thema"
    ))
    
    # 3. Simulatie Knoppen (NIEUW)
    # Deze knoppen triggeren de cloud logica met een 'simulation' vlag
    entities.append(AionLogicSimulationButton(
        coordinator, entry, "fire", "Test: Brandalarm", "mdi:fire-alert"
    ))
    entities.append(AionLogicSimulationButton(
        coordinator, entry, "intrusion", "Test: Inbraakalarm", "mdi:shield-alert"
    ))
    entities.append(AionLogicSimulationButton(
        coordinator, entry, "arrival", "Test: Thuiskomst (Auto)", "mdi:car-connected"
    ))

    async_add_entities(entities)


def _copy_file_to_config(hass: HomeAssistant, source_dir: str, source_file: str, dest_dir_name: str, dest_file_name: str) -> tuple[bool, str]:
    """Kopieert een bestand van de assets naar de config map, MET VEILIGHEIDSCHECK."""
    try:
        # Paden bepalen
        integration_dir = os.path.dirname(__file__)
        source_path = os.path.join(integration_dir, source_dir, source_file)
        
        config_dir = hass.config.path()
        dest_dir = os.path.join(config_dir, dest_dir_name)
        dest_path = os.path.join(dest_dir, dest_file_name)

        # CHECK 1: Bestaat het bronbestand?
        if not os.path.exists(source_path):
            return False, f"Bronbestand niet gevonden: {source_file}"

        # Maak map indien nodig
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)

        # --- CHECK 2: Bestaat het doelbestand al? (INSTALLATIE BESCHERMING) ---
        if os.path.exists(dest_path):
            # We overschrijven NIET, maar geven een foutmelding.
            return False, f"Veiligheid: Het bestand '{dest_file_name}' bestaat al. Hernoem of verwijder uw huidige bestand eerst."

        # Kopiëren (Alleen als het veilig is)
        shutil.copy2(source_path, dest_path)
        return True, "Succesvol geïnstalleerd."

    except Exception as e:
        _LOGGER.error(f"Fout bij kopiëren bestand: {e}")
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Aion Logic"
        )
        
        # Configureer paden op basis van type
        if blueprint_type == "theme":
            self._source_file = THEME_SOURCE_FILENAME
            self._dest_file = THEME_DESTINATION_FILENAME
            self._dest_dir = THEME_SOURCE_SUBDIR # themes/
            self._title = "Aion Logic Thema"
            
        elif blueprint_type == "dashboard":
            self._source_file = DASHBOARD_TEMPLATE_FILENAME
            self._dest_file = DASHBOARD_PUBLIC_FILENAME
            self._dest_dir = "www" # Gaat direct naar /config/www
            self._title = "Dashboard Template"

    async def async_press(self) -> None:
        """Actie bij indrukken."""
        # Bepaal de source directory (voor themes is het 'themes', voor de rest 'assets')
        src_dir = THEME_SOURCE_SUBDIR if self._blueprint_type == "theme" else DASHBOARD_SOURCE_SUBDIR
        
        success, message = await self.hass.async_add_executor_job(
            _copy_file_to_config,
            self.hass,
            src_dir,
            self._source_file,
            self._dest_dir,
            self._dest_file
        )

        if success:
            # Herlaad automations/themes indien nodig
            if self._blueprint_type == "theme":
                await self.hass.services.async_call("frontend", "reload_themes")
                
            await self.hass.services.async_call(
                "persistent_notification", "create", {
                    "title": f"Aion Logic: {self._title} Geïnstalleerd",
                    "message": (
                        f"Succes! {message}\n"
                        f"Indien nodig, herstart Home Assistant of ververs uw browser."
                    ),
                    "notification_id": f"aion_logic_install_{self._blueprint_type}",
                })
        else:
            # FOUT (Rood)
            await self.hass.services.async_call(
                "persistent_notification", "create", {
                    "title": f"Aion Logic: Installatie Gestopt",
                    "message": message, # Hier staat nu de veiligheidswaarschuwing
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Aion Logic"
        )

    async def async_press(self) -> None:
        """Stuur signaal naar coordinator."""
        await self._coordinator.async_run_simulation(self._sim_type)