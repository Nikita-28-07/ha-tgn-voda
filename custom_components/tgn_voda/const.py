DOMAIN = "tgn_voda"
PLATFORMS = ["sensor", "binary_sensor", "button"]

CONF_LOGIN = "login"
CONF_PASSWORD = "password"
CONF_ACCOUNT_ID = "account_id"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_CA_BUNDLE = "ca_bundle"
CONF_BASE_URL = "base_url"

DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = 1800
DEFAULT_CA_BUNDLE = ""
DEFAULT_BASE_URL = "https://lk.tgnvoda.ru"

EVENT_HISTORY = "tgn_voda_history"
EVENT_PAYMENTS = "tgn_voda_payments"
EVENT_ACCRUALS = "tgn_voda_accruals"
EVENT_ACCRUALS_DETALIZATION = "tgn_voda_accruals_detalization"
EVENT_COUNTER_INFO = "tgn_voda_counter_info"
