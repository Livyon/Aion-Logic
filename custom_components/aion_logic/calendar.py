"""Calendar platform for Aion Logic® (Multi-Proxy Robust)."""
import logging
from datetime import datetime, date

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_FAMILY_CALENDAR

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    config_source = entry.options.get(CONF_FAMILY_CALENDAR)
    source_entities = []
    if config_source:
        source_entities = [config_source] if isinstance(config_source, str) else config_source
    
    async_add_entities([AionLogicProxyCalendar(hass, entry, source_entities)])

class AionLogicProxyCalendar(CalendarEntity):
    def __init__(self, hass, entry, source_entities):
        self.hass = hass
        self._source_entities = source_entities
        self._attr_name = "Aion Gezinsagenda"
        self._attr_unique_id = f"{entry.entry_id}_family_calendar_proxy"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:calendar-multiselect"
        self._next_event = None

    @property
    def event(self) -> CalendarEvent | None:
        """Geeft het actuele of eerstvolgende event terug."""
        return self._next_event

    async def async_get_events(self, hass, start_date, end_date) -> list[CalendarEvent]:
        if not self._source_entities: return []
        all_events = []

        for source in self._source_entities:
            try:
                response = await hass.services.async_call(
                    "calendar", "get_events",
                    {"entity_id": source, "start_date_time": start_date, "end_date_time": end_date},
                    blocking=True, return_response=True,
                )
                if response and source in response:
                    for ev in response[source].get("events", []):
                        try:
                            # Gebruik helper voor consistente parsing
                            s = ev["start"]
                            e = ev["end"]
                            dt_s = dt_util.parse_datetime(s) if "T" in str(s) else dt_util.start_of_local_day(dt_util.parse_date(s))
                            dt_e = dt_util.parse_datetime(e) if "T" in str(e) else dt_util.start_of_local_day(dt_util.parse_date(e))

                            all_events.append(CalendarEvent(
                                summary=ev.get("summary", "Onbekend"),
                                start=dt_s, end=dt_e,
                                description=ev.get("description", ""),
                                location=ev.get("location", ""),
                            ))
                        except Exception as parse_err:
                            _LOGGER.warning(f"Parse error in {source}: {parse_err}")
            except Exception as e:
                _LOGGER.error(f"Fout bij ophalen {source}: {e}")

        # Robuuste sortering (forceer datetime voor vergelijking)
        all_events.sort(key=lambda x: x.start if isinstance(x.start, datetime) else datetime.combine(x.start, datetime.min.time()))
        
        # Update het 'hoofd-event' voor de entiteit status
        now = dt_util.now()
        upcoming = [e for e in all_events if (e.start if isinstance(e.start, datetime) else dt_util.start_of_local_day(e.start)) >= now]
        self._next_event = upcoming[0] if upcoming else (all_events[0] if all_events else None)
        
        return all_events
