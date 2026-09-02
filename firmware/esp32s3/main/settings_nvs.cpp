#include "settings_nvs.h"

#include <cstring>

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "settings";

#define NVS_NS "dgsettings"
#define KEY_DEVICE "device"
#define KEY_WIFI "wifi"
#define KEY_MQTT "mqtt"
#define KEY_CA "mqtt_ca"

static bool s_ready = false;

bool settings_store_init() {
    if (s_ready) return true;
    // board_wifi_init() calls nvs_flash_init() first and handles the erase path, so
    // this is normally a no-op. It is repeated because settings_store_init() must be
    // safe to call from anywhere - including from a build where Wi-Fi failed to come
    // up, which is exactly when someone will want to read the stored settings.
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        if (nvs_flash_erase() == ESP_OK) err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs unavailable (%s); settings will not persist",
                 esp_err_to_name(err));
        return false;
    }
    nvs_handle_t h;
    // Opened once here purely to fail early with a clear message. Every accessor
    // below opens its own handle: they are called from the HTTP task and from
    // app_main, and a shared handle would need a mutex for no gain.
    err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open(\"%s\"): %s", NVS_NS, esp_err_to_name(err));
        return false;
    }
    nvs_close(h);
    s_ready = true;
    return true;
}

bool settings_store_ready() { return s_ready; }

// --- blob helpers ----------------------------------------------------------
static size_t read_blob(const char *key, uint8_t *out, size_t cap) {
    if (!s_ready) return 0;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) return 0;
    size_t len = cap;
    const esp_err_t err = nvs_get_blob(h, key, out, &len);
    nvs_close(h);
    if (err == ESP_ERR_NVS_NOT_FOUND) return 0;      // a fresh board, not a fault
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "read \"%s\": %s", key, esp_err_to_name(err));
        return 0;
    }
    return len;
}

static bool write_blob(const char *key, const uint8_t *buf, size_t len) {
    if (!s_ready) {
        ESP_LOGW(TAG, "cannot persist \"%s\": nvs is unavailable", key);
        return false;
    }
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t err = nvs_set_blob(h, key, buf, len);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "write \"%s\": %s", key, esp_err_to_name(err));
        return false;
    }
    return true;
}

// --- identity and station --------------------------------------------------
bool settings_load_identity(DeviceIdentity *out) {
    if (out == nullptr) return false;
    uint8_t buf[DEVICE_BLOB_MAX];
    const size_t n = read_blob(KEY_DEVICE, buf, sizeof(buf));
    if (n == 0) return false;
    if (!device_identity_deserialize(buf, n, out)) {
        // Version bump, tightened validation, or a genuinely corrupt page. All three
        // mean the same thing to a caller and none of them is worth failing a boot
        // over, so say so once and carry on with the defaults.
        ESP_LOGW(TAG, "stored device identity rejected; using defaults");
        return false;
    }
    return true;
}

bool settings_save_identity(const DeviceIdentity &id) {
    uint8_t buf[DEVICE_BLOB_MAX];
    const size_t n = device_identity_serialize(id, buf, sizeof(buf));
    if (n == 0) return false;
    return write_blob(KEY_DEVICE, buf, n);
}

bool settings_load_wifi(WifiStaConfig *out) {
    if (out == nullptr) return false;
    uint8_t buf[WIFI_BLOB_MAX];
    const size_t n = read_blob(KEY_WIFI, buf, sizeof(buf));
    if (n == 0) return false;
    if (!wifi_sta_deserialize(buf, n, out)) {
        ESP_LOGW(TAG, "stored station config rejected; staying access-point only");
        return false;
    }
    return true;
}

bool settings_save_wifi(const WifiStaConfig &sta) {
    uint8_t buf[WIFI_BLOB_MAX];
    const size_t n = wifi_sta_serialize(sta, buf, sizeof(buf));
    if (n == 0) return false;
    return write_blob(KEY_WIFI, buf, n);
}

bool settings_clear_wifi() {
    if (!s_ready) return false;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t err = nvs_erase_key(h, KEY_WIFI);
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;   // already absent is success
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "erase \"%s\": %s", KEY_WIFI, esp_err_to_name(err));
        return false;
    }
    // Named explicitly, because the whole point of this function is what it does not
    // do, and a log line that says so is what somebody reads after pressing the
    // button by accident.
    ESP_LOGW(TAG, "station credentials erased; mqtt, device identity and the CA "
                  "certificate are untouched");
    return true;
}

// --- mqtt ------------------------------------------------------------------
bool settings_load_mqtt(MqttConfig *out) {
    if (out == nullptr) return false;
    uint8_t buf[MQTT_BLOB_MAX];
    const size_t n = read_blob(KEY_MQTT, buf, sizeof(buf));
    if (n == 0) return false;
    if (!mqtt_config_deserialize(buf, n, out)) {
        ESP_LOGW(TAG, "stored mqtt config rejected; using defaults (publishing off)");
        return false;
    }
    return true;
}

bool settings_save_mqtt(const MqttConfig &cfg) {
    uint8_t buf[MQTT_BLOB_MAX];
    const size_t n = mqtt_config_serialize(cfg, buf, sizeof(buf));
    if (n == 0) return false;
    return write_blob(KEY_MQTT, buf, n);
}

// --- the CA certificate ----------------------------------------------------
// Stored as a string rather than a blob, because it is one, and because
// nvs_get_str's length-probe call is exactly what settings_ca_bytes() needs.
size_t settings_load_ca(char *out, size_t out_cap) {
    if (out == nullptr || out_cap == 0) return 0;
    out[0] = '\0';
    if (!s_ready) return 0;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) return 0;
    size_t len = out_cap;
    const esp_err_t err = nvs_get_str(h, KEY_CA, out, &len);
    nvs_close(h);
    if (err != ESP_OK) {
        out[0] = '\0';
        return 0;
    }
    return len > 0 ? len - 1 : 0;      // nvs counts the NUL; callers do not
}

size_t settings_ca_bytes() {
    if (!s_ready) return 0;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) return 0;
    size_t len = 0;
    const esp_err_t err = nvs_get_str(h, KEY_CA, nullptr, &len);
    nvs_close(h);
    if (err != ESP_OK || len == 0) return 0;
    return len - 1;
}

bool settings_save_ca(const char *pem) {
    if (!s_ready || pem == nullptr) return false;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t err = nvs_set_str(h, KEY_CA, pem);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "write \"%s\": %s", KEY_CA, esp_err_to_name(err));
        return false;
    }
    return true;
}

bool settings_clear_ca() {
    if (!s_ready) return false;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t err = nvs_erase_key(h, KEY_CA);
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;   // already absent is success
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err == ESP_OK;
}
