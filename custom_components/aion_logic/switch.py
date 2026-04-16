"""Switch platform voor Aion Logic™ (Legacy Base v2.2 + Guard v2.3)."""
import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN, 
    SWITCH_COMING_HOME,
    SWITCH_GUEST_MODE,
    SWITCH_GUARD_PAUSE,
    SWITCH_GUARD_MASTER
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Zet de Aion Logic switches op."""
    entities = []

    # 1. De originele v2.2 switches
    entities.append(AionLogicInternalSwitch(
        entry, SWITCH_COMING_HOME, "Aion Coming Home", "mdi:car-convertible", 
        "Schakel in als u naar huis vertrekt."
    ))
    entities.append(AionLogicInternalSwitch(
        entry, SWITCH_GUEST_MODE, "Aion Guest Mode", "mdi:account-group", 
        "Schakel in als er gasten zijn."
    ))

    # 2. De NIEUWE Guard Switches (Surgical Add)
    entities.append(AionLogicPauseSwitch(
        entry, SWITCH_GUARD_PAUSE, "Aion Guard Pauze", "mdi:window-open-variant",
        "Pauzeert de beveiliging voor 15 minuten."
    ))
    entities.append(AionLogicInternalSwitch(
        entry, SWITCH_GUARD_MASTER, "Aion Guard Master", "mdi:shield-lock",
        "Manuele hoofdschakelaar."
    ))

    async_add_entities(entities)

class AionLogicInternalSwitch(SwitchEntity, RestoreEntity):
    """Standaard Aion Schakelaar (v2.2 Base)."""
    def __init__(self, entry, switch_type, name, icon, description):
        self._entry = entry
        self._switch_type = switch_type
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{switch_type}"
        self._attr_is_on = False
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Aion Logic")
        self._attr_extra_state_attributes = {"description": description}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = (last_state.state == "on")

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()

# --- NIEUW: De Timer Logic Class ---
class AionLogicPauseSwitch(AionLogicInternalSwitch):
    """Speciale schakelaar die zichzelf uitzet na 15 minuten."""

    def __init__(self, entry, switch_type, name, icon, description):
        super().__init__(entry, switch_type, name, icon, description)
        self._timer_remove = None

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self.async_write_ha_state()
        
        # Reset vorige timer
        if self._timer_remove:
            self._timer_remove()
        
        # Start timer: 15 minuten (900 seconden)
        self._timer_remove = async_call_later(self.hass, 900, self._async_timer_finished)

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        if self._timer_remove:
            self._timer_remove()
            self._timer_remove = None
        self.async_write_ha_state()

    @callback
    def _async_timer_finished(self, _):
        self._attr_is_on = False
        self._timer_remove = None
        self.async_write_ha_state()