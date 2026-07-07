"""Tests unitaires pour modules/sensor_store.py — SensorStore."""
import time
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

ZIGBEE_DEVICES_FIXTURE = {
    "salon_sensor":   {"room": "salon",  "type": "SNZB-02P"},
    "bureau_sensor":  {"room": "bureau", "type": "SNZB-02P"},
    "plant_sensor_1": {"plant": "ficus", "type": "SGS01Z"},
    "window_sensor_1": {"window": "salon-fenetre-1", "type": "SNZB-04"},
}


@pytest.fixture
def store():
    """SensorStore fraîche avec data_cache et ZIGBEE_DEVICES mockés."""
    with (
        patch("modules.sensor_store.data_cache") as mock_dc,
        patch("modules.sensor_store.CFG") as mock_cfg,
    ):
        mock_cfg.ZIGBEE_DEVICES = ZIGBEE_DEVICES_FIXTURE
        from modules.sensor_store import SensorStore
        s = SensorStore()
        s._mock_dc = mock_dc
        yield s


# ── update() ──────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_stores_payload_for_zigbee_topic(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.4, "humidity": 58})
        with store._lock:
            data = store._data.get("salon_sensor")
        assert data is not None
        assert data["temperature"] == 21.4
        assert data["humidity"] == 58

    def test_ignores_topic_with_three_parts(self, store):
        store.update("zigbee2mqtt/salon_sensor/availability", {"state": "online"})
        with store._lock:
            assert "salon_sensor" not in store._data

    def test_ignores_single_part_topic(self, store):
        store.update("zigbee2mqtt", {"something": 1})
        with store._lock:
            assert store._data == {}

    def test_ignores_bridge_device(self, store):
        store.update("zigbee2mqtt/bridge", {"state": "online"})
        with store._lock:
            assert "bridge" not in store._data

    def test_sets_timestamp_on_update(self, store):
        before = time.time()
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 20.0})
        after = time.time()
        with store._lock:
            ts = store._data["salon_sensor"]["_ts"]
        assert before <= ts <= after

    def test_merges_payload_with_existing_data(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        store.update("zigbee2mqtt/salon_sensor", {"humidity": 55})
        with store._lock:
            d = store._data["salon_sensor"]
        assert "temperature" in d
        assert "humidity" in d

    def test_soil_moisture_persisted_to_data_cache(self, store):
        store.update("zigbee2mqtt/plant_sensor_1", {"soil_moisture": 42.0, "battery": 98})
        assert store._mock_dc.log_raw.call_count >= 1
        logged_series = [c.args[0] for c in store._mock_dc.log_raw.call_args_list]
        assert any("plant_ficus_soil_moisture" in n for n in logged_series)
        write_keys = [c.args[0] for c in store._mock_dc.write.call_args_list]
        assert any("plant.ficus.soil_moisture" in k for k in write_keys)

    def test_temperature_persisted_to_data_cache(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        assert store._mock_dc.log_raw.call_count >= 1
        logged_series = [c.args[0] for c in store._mock_dc.log_raw.call_args_list]
        assert any("sensor_salon_temperature" in n for n in logged_series)

    def test_contact_persisted_to_data_cache(self, store):
        store.update("zigbee2mqtt/window_sensor_1", {"contact": False, "battery": 90})
        write_keys = [c.args[0] for c in store._mock_dc.write.call_args_list]
        assert any("window.salon-fenetre-1.contact" in k for k in write_keys)
        assert store._mock_dc.log_raw.call_count >= 1
        logged_series = [c.args[0] for c in store._mock_dc.log_raw.call_args_list]
        assert any("window_salon-fenetre-1_contact" in n for n in logged_series)


# ── get_field() ───────────────────────────────────────────────────────────────

class TestGetField:
    def test_returns_value_when_fresh(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.4})
        val = store.get_field("salon_sensor", "temperature")
        assert val == pytest.approx(21.4)

    def test_returns_none_for_unknown_device(self, store):
        assert store.get_field("nonexistent", "temperature") is None

    def test_returns_none_for_missing_field(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        assert store.get_field("salon_sensor", "humidity") is None

    def test_returns_none_when_expired(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        with store._lock:
            store._data["salon_sensor"]["_ts"] = time.time() - 1000
        assert store.get_field("salon_sensor", "temperature", max_age=900) is None

    def test_returns_value_when_within_max_age(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        with store._lock:
            store._data["salon_sensor"]["_ts"] = time.time() - 500
        val = store.get_field("salon_sensor", "temperature", max_age=900)
        assert val is not None


# ── get_room_value() / get_plant_value() ──────────────────────────────────────

class TestRoomPlantLookup:
    def test_get_room_value_maps_room_to_device(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 22.0})
        val = store.get_room_value("salon", "temperature")
        assert val == pytest.approx(22.0)

    def test_get_room_value_returns_none_for_unknown_room(self, store):
        assert store.get_room_value("cuisine", "temperature") is None

    def test_get_room_value_returns_none_when_no_data(self, store):
        assert store.get_room_value("salon", "temperature") is None

    def test_get_plant_value_maps_plant_to_device(self, store):
        store.update("zigbee2mqtt/plant_sensor_1", {"soil_moisture": 42.0})
        val = store.get_plant_value("ficus")
        assert val == pytest.approx(42.0)

    def test_get_plant_value_returns_none_for_unknown_plant(self, store):
        assert store.get_plant_value("monstera") is None

    def test_get_window_value_maps_window_to_device(self, store):
        store.update("zigbee2mqtt/window_sensor_1", {"contact": True})
        assert store.get_window_value("salon-fenetre-1") is True

    def test_get_window_value_reflects_open_state(self, store):
        store.update("zigbee2mqtt/window_sensor_1", {"contact": False})
        assert store.get_window_value("salon-fenetre-1") is False

    def test_get_window_value_returns_none_for_unknown_window(self, store):
        assert store.get_window_value("chambre1-fenetre-1") is None


# ── unmapped_devices() / mapped_active_count() ────────────────────────────────

class TestDiscovery:
    def test_unmapped_devices_excludes_configured(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        unmapped = store.unmapped_devices()
        assert "salon_sensor" not in unmapped

    def test_unmapped_devices_includes_unknown(self, store):
        store.update("zigbee2mqtt/unknown_device", {"linkquality": 100})
        unmapped = store.unmapped_devices()
        assert "unknown_device" in unmapped

    def test_known_devices_lists_all_seen(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        store.update("zigbee2mqtt/bureau_sensor", {"temperature": 19.0})
        devices = store.known_devices()
        assert "salon_sensor" in devices
        assert "bureau_sensor" in devices

    def test_mapped_active_count_with_fresh_data(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        assert store.mapped_active_count() == 1

    def test_mapped_active_count_ignores_stale(self, store):
        store.update("zigbee2mqtt/salon_sensor", {"temperature": 21.0})
        with store._lock:
            store._data["salon_sensor"]["_ts"] = time.time() - 1000
        assert store.mapped_active_count(max_age=900) == 0

    def test_mapped_active_count_zero_when_no_data(self, store):
        assert store.mapped_active_count() == 0


# ── _plant_id_for() ───────────────────────────────────────────────────────────

class TestPlantIdFor:
    def test_returns_configured_plant_id(self, store):
        pid = store._plant_id_for("plant_sensor_1")
        assert pid == "ficus"

    def test_returns_safe_id_for_unconfigured_device(self, store):
        pid = store._plant_id_for("unknown device!")
        assert pid == "unknown_device_"

    def test_caches_lookup_result(self, store):
        store._plant_id_for("plant_sensor_1")
        store._plant_id_for("plant_sensor_1")
        assert "plant_sensor_1" in store._device_to_plant
