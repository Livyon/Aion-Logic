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
DASHBOARD_BASE_FILENAME = "dashboard_base.yaml" # <--- NIEUW: Aangepaste naam
DASHBOARD_SOURCE_SUBDIR = "assets"
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

def _get_room_icon(room_name: str) -> str:
    """Smart Icon Mapper: Kiest automatisch het juiste icoon op basis van de naam."""
    name_lower = room_name.lower()
    if "woonkamer" in name_lower or "living" in name_lower: return "mdi:sofa"
    if "keuken" in name_lower: return "mdi:chef-hat"
    if "eetkamer" in name_lower: return "mdi:table-furniture"
    if "badkamer" in name_lower: return "mdi:shower"
    if "slaapkamer" in name_lower: return "mdi:bed"
    if "kantoor" in name_lower or "bureau" in name_lower: return "mdi:desk"
    if "gang" in name_lower or "hal" in name_lower or "inkom" in name_lower: return "mdi:stairs-up"
    if "berging" in name_lower or "garage" in name_lower: return "mdi:package-variant-closed"
    if "tuin" in name_lower or "buiten" in name_lower: return "mdi:tree"
    return "mdi:door-open" # Fallback

def _generate_and_save_dashboard(hass: HomeAssistant, entry: ConfigEntry, area_names: dict) -> tuple[bool, str]:
    """Leest configuratie, genereert YAML per kamer, en injecteert dit in de base-template."""
    try:
        integration_dir = os.path.dirname(__file__)
        base_file_path = os.path.join(integration_dir, DASHBOARD_SOURCE_SUBDIR, DASHBOARD_BASE_FILENAME)
        
        dest_dir = os.path.join(hass.config.path(), "custom_components", DOMAIN, "www")
        dest_path = os.path.join(dest_dir, DASHBOARD_PUBLIC_FILENAME)
        
        if not os.path.exists(base_file_path):
            return False, f"Basis template '{DASHBOARD_BASE_FILENAME}' ontbreekt in assets map."
            
        if os.path.exists(dest_path):
            return False, "Veiligheid: Het dashboard bestaat al. Hernoem of verwijder uw huidige 'aion_logic-dashboard-code.txt' eerst."
            
        with open(base_file_path, "r", encoding="utf-8") as f:
            base_content = f.read()
            
        rooms_yaml = ""
        
        ROOM_TEMPLATE = """
      - type: grid
        cards:
          - type: custom:bubble-card
            card_type: button
            button_type: name
            name: ##ZONE_NAME##
            icon: ##ZONE_ICON##
            button_action:
              tap_action:
                action: navigate
                navigation_path: '###ZONE_HASH##'
            sub_button:
              main: []
              bottom: []
            card_layout: normal
            card_mod:
              style: |
                .bubble-icon {
                  color: {{ 'var(--active-state-color)' if is_state('binary_sensor.aion_logic_aion_active_##ZONE_SLUG##', 'on') else 'var(--state-icon-color)' }} !important;
                  opacity: {{ '1' if is_state('binary_sensor.aion_logic_aion_active_##ZONE_SLUG##', 'on') else '0.5' }} !important;
                }
          - type: vertical-stack
            cards:
              - type: custom:bubble-card
                card_type: pop-up
                hash: '###ZONE_HASH##'
                name: ##ZONE_NAME## Bediening
                icon: ##ZONE_ICON##
                margin_top_mobile: 56px
                button_type: name
                auto_close: 15000
                sub_button:
                  main: []
                  bottom: []
                card_layout: normal
              - type: custom:auto-entities
                card:
                  type: grid
                  columns: 1
                  square: false
                card_param: cards
                filter:
                  include:
                    - domain: climate
                      area: ##ZONE_NAME##
                      options:
                        type: custom:bubble-card
                        card_type: climate
                        sub_button:
                          - name: Mode
                            select_attribute: hvac_modes
                            show_arrow: false
                        show_attribute: true
                        attribute: current_temperature
              - type: custom:auto-entities
                card:
                  type: grid
                  columns: 2
                  square: false
                card_param: cards
                filter:
                  template: >
                    {% set target_area = '##ZONE_NAME##' %}
                    {% set ns = namespace(cards=[]) %}
                    {% set domains =[states.light, states.switch] %}
                    {% for domain in domains %}
                      {% for entity in domain %}
                        {% set entity_area = area_name(entity.entity_id) %}
                        {% if entity_area and entity_area | string | lower | trim == target_area | lower | trim %}
                          {% set button_type = 'switch' %}
                          {% if entity.domain == 'light' %}
                            {% set modes = entity.attributes.supported_color_modes | default([]) %}
                            {% if 'brightness' in modes or 'hs' in modes or 'xy' in modes or 'color_temp' in modes or 'rgb' in modes %}
                              {% set button_type = 'slider' %}
                            {% endif %}
                          {% endif %}
                          {% set card_config = {
                            'type': 'custom:bubble-card',
                            'card_type': 'button',
                            'button_type': button_type,
                            'entity': entity.entity_id,
                            'name': entity.name, 
                            'show_icon': true,
                            'icon': 'mdi:lightbulb',
                            'scrolling_effect': false,
                            'styles': ".bubble-icon, .name {color: white !important;}"
                          } %}
                          {% set ns.cards = ns.cards +[card_config] %}
                        {% endif %}
                      {% endfor %}
                    {% endfor %}
                    {{ ns.cards | sort(attribute='name') }}
              - type: custom:auto-entities
                card:
                  type: grid
                  columns: 1
                  square: false
                card_param: cards
                filter:
                  template: |
                    {% set target_area = '##ZONE_NAME##' %}
                    {% set ns = namespace(cards=[]) %}
                    {% for cov in states.cover %}
                      {% set entity_area = area_name(cov.entity_id) %}
                      {% if entity_area and entity_area | string | lower | trim == target_area | lower | trim %}
                        {% set dev_class = cov.attributes.device_class | default('unknown') %}
                        {% if dev_class != 'garage' and dev_class != 'gate' %}
                          {% set card_config = {
                            'type': 'custom:bubble-card',
                            'card_type': 'cover',
                            'entity': cov.entity_id,
                            'name': cov.name,
                            'show_icon': true,
                            'sub_button': []
                          } %}
                          {% set ns.cards = ns.cards + [card_config] %}
                        {% endif %}
                      {% endif %}
                    {% endfor %}
                    {{ ns.cards | sort(attribute='name') }}
                show_empty: false
              - type: custom:auto-entities
                card:
                  type: grid
                  columns: 2
                  square: false
                card_param: cards
                filter:
                  template: >
                    {% set target_area = '##ZONE_NAME##' %}
                    {% set ns = namespace(cards=[]) %}
                    {% set target_classes = ['window', 'door', 'garage_door', 'opening'] %}
                    {% for sensor in states.binary_sensor %}
                      {% set current_area = area_name(sensor.entity_id) %}
                      {% if current_area and current_area | string | lower | trim == target_area | lower | trim %}
                        {% set dev_class = sensor.attributes.device_class | default('unknown') %}
                        {% if dev_class in target_classes %}
                          {% set card_config = {
                            'type': 'custom:bubble-card',
                            'card_type': 'button',
                            'button_type': 'state',
                            'entity': sensor.entity_id,
                            'name': sensor.name,
                            'show_last_changed': true,
                            'card_mod': {
                              'style': ".bubble-icon { color: " + ('var(--error-color)' if sensor.state == 'on' else 'var(--state-icon-color)') + " !important; opacity: " + ('1' if sensor.state == 'on' else '0.5') + " !important; }"
                            }
                          } %}
                          {% set ns.cards = ns.cards + [card_config] %}
                        {% endif %}
                      {% endif %}
                    {% endfor %}
                    {{ ns.cards | sort(attribute='state', reverse=true) }}
"""
        rooms =[]
        for key, zone_cfg in entry.options.items():
            if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
                area_id = key.replace("area_", "").replace("zone_", "")
                
                # OUDE naam uit Aion geheugen (Cruciaal: dit koppelt aan de binary_sensor!)
                config_zone_name = zone_cfg.get("zone_name", area_id)
                zone_slug = config_zone_name.lower().replace(" ", "_")
                
                # NIEUWE live naam uit Home Assistant (Cruciaal: dit is voor weergave en apparaten)
                display_name = area_names.get(area_id, config_zone_name)
                
                rooms.append((display_name, zone_slug))
                
        rooms.sort(key=lambda x: x[0]) # Sorteer alfabetisch op display naam
        
        for display_name, zone_slug in rooms:
            zone_hash = "".join(filter(str.isalnum, display_name)) 
            zone_icon = _get_room_icon(display_name)
            
            room_block = ROOM_TEMPLATE.replace("##ZONE_NAME##", display_name)
            room_block = room_block.replace("##ZONE_SLUG##", zone_slug)
            room_block = room_block.replace("##ZONE_HASH##", zone_hash)
            room_block = room_block.replace("##ZONE_ICON##", zone_icon)
            
            rooms_yaml += room_block
            
        final_content = base_content.replace("##AION_ROOMS_PLACEHOLDER##", rooms_yaml)
        
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
            
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(final_content)
            
        link_url = f"/{DOMAIN}_assets/{DASHBOARD_PUBLIC_FILENAME}"
        return True, f"Het dashboard is op maat gegenereerd voor {len(rooms)} kamers.\n\n👉 [**RECHTERMUISKNOP HIER -> OPEN IN NIEUW TABBLAD**]({link_url})\n\n*(Op mobiel: houd de link lang ingedrukt)*"
        
    except Exception as e:
        _LOGGER.error(f"Fout bij genereren dashboard: {e}")
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
            
            # Start de nieuwe SaaS Auto-Builder met de actuele namen
            success, message = await self.hass.async_add_executor_job(
                _generate_and_save_dashboard, self.hass, self._entry, area_names
            )
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
