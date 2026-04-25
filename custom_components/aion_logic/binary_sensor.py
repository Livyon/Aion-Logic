"""Binaire sensoren voor Aion Logic™."""
import logging
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Zet de Aion Logic binaire sensoren op."""
    entities =[]
    entity_reg = er.async_get(hass)
    
    for key, zone_cfg in entry.options.items():
        if (key.startswith("area_") or key.startswith("zone_")) and isinstance(zone_cfg, dict):
            area_id = key.replace("area_", "").replace("zone_", "")
            zone_name = zone_cfg.get("zone_name", area_id)
            
            # Zoek alle lichten en schakelaars in deze specifieke ruimte
            area_entities =[
                ent.entity_id for ent in entity_reg.entities.values()
                if ent.area_id == area_id and ent.domain in ["light", "switch"]
            ]
            
            # Voeg ook handmatig geselecteerde lichten toe (indien gekozen in de instellingen)
            explicit_lights = zone_cfg.get("lighting_entities",[])
            all_targets = list(set(area_entities + explicit_lights))
            
            if all_targets:
                entities.append(AionAreaActiveSensor(hass, entry, area_id, zone_name, all_targets))
                
    async_add_entities(entities)

class AionAreaActiveSensor(BinarySensorEntity):
    """Berekent razendsnel in de backend of een ruimte 'actief' (verlicht) is."""
    _attr_should_poll = False
    _attr_icon = "mdi:lightbulb-group"

    def __init__(self, hass, entry, area_id, zone_name, target_entities):
        self.hass = hass
        self._entry = entry
        self._area_id = area_id
        self._target_entities = target_entities
        
        # Voorbeeld entity_id: binary_sensor.aion_active_woonkamer
        safe_name = zone_name.lower().replace(" ", "_")
        self._attr_name = f"Aion Active {zone_name}"
        self._attr_unique_id = f"{entry.entry_id}_active_{area_id}"
        self._attr_is_on = False
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self):
        """Registreer listeners bij opstarten."""
        await super().async_added_to_hass()
        self._update_state()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._target_entities, self._handle_state_change
            )
        )

    @callback
    def _handle_state_change(self, event):
        """Wordt getriggerd bij een licht wijziging."""
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _update_state(self):
        """Itereer kort over de entiteiten in het geheugen."""
        is_active = False
        for ent_id in self._target_entities:
            state = self.hass.states.get(ent_id)
            if state and state.state == "on":
                is_active = True
                break
        self._attr_is_on = is_active