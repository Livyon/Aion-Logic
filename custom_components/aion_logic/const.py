"""Constanten voor de Aion Logic™ integratie."""

DOMAIN = "aion_logic"

# Config Flow Keys
CONF_ACTIVATION_CODE = "activation_code"
CONF_TRAVEL_SENSOR = "travel_time_sensor"
CONF_ENERGY_SENSOR = "energy_sensor"
CONF_ENERGY_TAG = "energy_tag_value"
CONF_FAMILY_CALENDAR = "family_calendar_entity"

# BLE GHOST MASKING
GHOST_WINDOW_SECONDS = 180

# Early Bird
CONF_EARLY_BIRD_SENSORS = "early_bird_sensors"
CONF_EARLY_BIRD_WINDOW = "early_bird_window"

# Drivers
CONF_DRIVER_1_NAME = "driver_1_name"
CONF_DRIVER_1_SENSOR = "driver_1_sensor"
CONF_DRIVER_1_TRIGGER = "driver_1_trigger"
CONF_DRIVER_1_NOTIFY = "driver_1_notify"

CONF_DRIVER_2_NAME = "driver_2_name"
CONF_DRIVER_2_SENSOR = "driver_2_sensor"
CONF_DRIVER_2_TRIGGER = "driver_2_trigger"
CONF_DRIVER_2_NOTIFY = "driver_2_notify"

CONF_DRIVER_OUTDOOR_MAX = "driver_outdoor_max"
CONF_DRIVER_INDOOR_MAX = "driver_indoor_max"

# Lifestyle
CONF_LS_COMING_HOME_ON = "ls_coming_home_on"
CONF_LS_COMING_HOME_SCENE = "ls_coming_home_scene"
CONF_LS_COMING_HOME_BRIGHTNESS = "ls_coming_home_brightness"
CONF_LS_LEAVING_HOME_OFF = "ls_leaving_home_off"
CONF_LS_LEAVING_HOME_SCENE = "ls_leaving_home_scene"
CONF_LS_NIGHT_OFF = "ls_night_off"
CONF_LS_NIGHT_OFF_SCENE = "ls_night_off_scene"
CONF_LS_NIGHT_ON = "ls_night_on"
CONF_LS_NIGHT_ON_SCENE = "ls_night_on_scene"
CONF_LS_NIGHT_ON_BRIGHTNESS = "ls_night_on_brightness"
CONF_LS_MORNING_ON = "ls_morning_on"
CONF_LS_MORNING_SCENE = "ls_morning_scene"
CONF_LS_MORNING_BRIGHTNESS = "ls_morning_brightness"
CONF_LS_SUN_CHECK = "ls_sun_check"
CONF_LS_COMING_HOME_AUTO = "ls_coming_home_auto"
CONF_LS_LEAVING_HOME_AUTO = "ls_leaving_home_auto"
CONF_LS_NIGHT_AUTO = "ls_night_auto"
CONF_LS_MORNING_AUTO = "ls_morning_auto"

# --- NIEUW: Guard & Safety (Surgical Implant v2.3) ---
CONF_GUARD_MODE = "guard_mode"
GUARD_MODE_AUTONOMOUS = "autonomous"
GUARD_MODE_MANUAL = "manual"
GUARD_MODE_DISABLED = "disabled"
CONF_ALARM_PANEL = "alarm_panel_entity"
CONF_DEFENSE_LIGHTS = "defense_lights"
CONF_DEFENSE_SPEAKERS = "defense_speakers"
CONF_ALARM_MSG = "alarm_message"
CONF_SECURITY_NOTIFY = "security_notify_service"
CONF_EMERGENCY_CONTACTS = "emergency_contacts"

# 2. Brand (Fireman's Stop)
CONF_SMOKE_SENSORS = "smoke_sensors"
CONF_FIRE_LIGHTS = "fire_lights"
CONF_FIRE_SHUTTERS = "fire_shutters"

# Switches
SWITCH_COMING_HOME = "coming_home"
SWITCH_GUEST_MODE = "guest_mode"
SWITCH_GUARD_PAUSE = "guard_pause"
SWITCH_GUARD_MASTER = "guard_master"

# Ventilation & Air Quality
CONF_CENTRAL_VENT = "central_ventilation_unit"
CONF_ENABLE_HUMIDITY = "enable_humidity_control"
CONF_ENABLE_NIGHT_VENT = "enable_night_ventilation"
CONF_ZONE_VENT = "zone_ventilation"
CONF_HUMIDITY_SENSOR = "humidity_sensor"

# API
AION_LOGIC_GATEWAY_URL = "https://aion-logic-gateway-585589675470.europe-west1.run.app"
AION_LOGIC_VERIFY_URL = "https://verify-license-585589675470.europe-west1.run.app"
