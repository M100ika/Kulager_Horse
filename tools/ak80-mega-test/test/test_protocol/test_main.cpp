// Тесты кодирования и декодирования кадров MIT-режима AK80-64.
// Железо не нужно: pio test -e native. На Mega они ловят ошибки 16-битного int.

#include <unity.h>

#include "ak80_mit.h"

#ifdef ARDUINO
#include <Arduino.h>
#endif

using ak80::kAK80_64;

void setUp() {}
void tearDown() {}

static void test_float_to_uint_endpoints_and_clamping() {
  TEST_ASSERT_EQUAL_UINT16(0, ak80::float_to_uint(-12.5f, -12.5f, 12.5f, 16));
  TEST_ASSERT_EQUAL_UINT16(65535, ak80::float_to_uint(12.5f, -12.5f, 12.5f, 16));
  TEST_ASSERT_EQUAL_UINT16(0, ak80::float_to_uint(-100.0f, -12.5f, 12.5f, 16));
  TEST_ASSERT_EQUAL_UINT16(65535, ak80::float_to_uint(100.0f, -12.5f, 12.5f, 16));
  TEST_ASSERT_EQUAL_UINT16(4095, ak80::float_to_uint(8.0f, -8.0f, 8.0f, 12));
  TEST_ASSERT_EQUAL_UINT16(0, ak80::float_to_uint(-1.0f, 0.0f, 500.0f, 12));
}

// После кодирования и декодирования ошибка около половины шага квантования
// (запас на погрешность float).
static void check_roundtrip(float x_min, float x_max, uint8_t bits) {
  const float lsb = (x_max - x_min) / ((1UL << bits) - 1);
  for (uint8_t i = 0; i <= 100; i++) {
    const float x = x_min + (x_max - x_min) * i / 100.0f;
    const uint16_t raw = ak80::float_to_uint(x, x_min, x_max, bits);
    TEST_ASSERT_FLOAT_WITHIN(lsb * 0.6f, x,
                             ak80::uint_to_float(raw, x_min, x_max, bits));
  }
}

static void test_roundtrip_all_fields() {
  check_roundtrip(kAK80_64.p_min, kAK80_64.p_max, 16);
  check_roundtrip(kAK80_64.v_min, kAK80_64.v_max, 12);
  check_roundtrip(kAK80_64.t_min, kAK80_64.t_max, 12);
  check_roundtrip(kAK80_64.kp_min, kAK80_64.kp_max, 12);
  check_roundtrip(kAK80_64.kd_min, kAK80_64.kd_max, 12);
}

static void test_pack_command_layout_min_max() {
  // p = 0x0000, v = 0xFFF, kp = 0x000, kd = 0xFFF, t = 0x000
  const ak80::Command cmd = {-12.5f, 8.0f, 0.0f, 5.0f, -144.0f};
  const uint8_t expected[8] = {0x00, 0x00, 0xFF, 0xF0, 0x00, 0xFF, 0xF0, 0x00};
  uint8_t out[8];
  ak80::pack_command(cmd, out);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, out, 8);
}

static void test_pack_command_layout_max_min() {
  // p = 0xFFFF, v = 0x000, kp = 0xFFF, kd = 0x000, t = 0xFFF
  const ak80::Command cmd = {12.5f, -8.0f, 500.0f, 0.0f, 144.0f};
  const uint8_t expected[8] = {0xFF, 0xFF, 0x00, 0x0F, 0xFF, 0x00, 0x0F, 0xFF};
  uint8_t out[8];
  ak80::pack_command(cmd, out);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, out, 8);
}

static void test_pack_command_clamps_out_of_range() {
  const ak80::Command wild = {99.0f, -99.0f, 9999.0f, -1.0f, 9999.0f};
  const ak80::Command edge = {12.5f, -8.0f, 500.0f, 0.0f, 144.0f};
  uint8_t a[8], b[8];
  ak80::pack_command(wild, a);
  ak80::pack_command(edge, b);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(b, a, 8);
}

static void test_zero_command_gives_near_zero_torque() {
  uint8_t out[8];
  ak80::pack_command({0.0f, 0.0f, 0.0f, 0.0f, 0.0f}, out);
  const uint16_t kp = (static_cast<uint16_t>(out[3] & 0xF) << 8) | out[4];
  const uint16_t kd = (static_cast<uint16_t>(out[5]) << 4) | (out[6] >> 4);
  const uint16_t t = (static_cast<uint16_t>(out[6] & 0xF) << 8) | out[7];
  TEST_ASSERT_EQUAL_UINT16(0, kp);
  TEST_ASSERT_EQUAL_UINT16(0, kd);
  TEST_ASSERT_FLOAT_WITHIN(0.05f, 0.0f, ak80::uint_to_float(t, -144.0f, 144.0f, 12));
}

static void test_special_frames() {
  const uint8_t enter[8] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFC};
  const uint8_t exit_[8] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFD};
  const uint8_t zero[8] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFE};
  uint8_t out[8];
  ak80::make_enter_motor_mode(out);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(enter, out, 8);
  ak80::make_exit_motor_mode(out);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(exit_, out, 8);
  ak80::make_set_zero(out);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(zero, out, 8);
}

static void test_unpack_full_reply() {
  // ID 1, p = 0xFFFF, v = 0x000, t = 0xFFF, 25 °C, без ошибок
  const uint8_t data[8] = {0x01, 0xFF, 0xFF, 0x00, 0x0F, 0xFF, 65, 0};
  ak80::Feedback fb;
  TEST_ASSERT_TRUE(ak80::unpack_reply(data, 8, fb));
  TEST_ASSERT_EQUAL_UINT8(1, fb.motor_id);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 12.5f, fb.position);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, -8.0f, fb.velocity);
  TEST_ASSERT_FLOAT_WITHIN(1e-2f, 144.0f, fb.torque);
  TEST_ASSERT_TRUE(fb.has_diag);
  TEST_ASSERT_EQUAL_INT16(25, fb.temperature_c);
  TEST_ASSERT_EQUAL_UINT8(0, fb.error);
}

static void test_unpack_midscale_is_near_zero() {
  // p = 0x8000, v = 0x800, t = 0x800
  const uint8_t data[8] = {0x02, 0x80, 0x00, 0x80, 0x08, 0x00, 40, 5};
  ak80::Feedback fb;
  TEST_ASSERT_TRUE(ak80::unpack_reply(data, 8, fb));
  TEST_ASSERT_EQUAL_UINT8(2, fb.motor_id);
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 0.0f, fb.position);
  TEST_ASSERT_FLOAT_WITHIN(5e-3f, 0.0f, fb.velocity);
  TEST_ASSERT_FLOAT_WITHIN(0.05f, 0.0f, fb.torque);
  TEST_ASSERT_EQUAL_INT16(0, fb.temperature_c);
  TEST_ASSERT_EQUAL_UINT8(5, fb.error);
}

static void test_unpack_short_reply_without_diag() {
  const uint8_t data[6] = {0x01, 0x80, 0x00, 0x80, 0x08, 0x00};
  ak80::Feedback fb;
  TEST_ASSERT_TRUE(ak80::unpack_reply(data, 6, fb));
  TEST_ASSERT_FALSE(fb.has_diag);
  TEST_ASSERT_EQUAL_UINT8(0, fb.error);
}

static void test_unpack_rejects_truncated_frame() {
  const uint8_t data[5] = {0x01, 0x80, 0x00, 0x80, 0x08};
  ak80::Feedback fb;
  TEST_ASSERT_FALSE(ak80::unpack_reply(data, 5, fb));
}

static void test_command_survives_roundtrip_through_reply_decoder() {
  // Позиция, скорость и момент в команде и в ответе кодируются одинаково.
  uint8_t cmd[8];
  ak80::pack_command({1.234f, -2.5f, 0.0f, 0.0f, 10.0f}, cmd);
  const uint8_t reply[8] = {
      0x01,
      cmd[0],
      cmd[1],
      cmd[2],
      static_cast<uint8_t>((cmd[3] & 0xF0) | (cmd[6] & 0x0F)),
      cmd[7],
      40,
      0};
  ak80::Feedback fb;
  TEST_ASSERT_TRUE(ak80::unpack_reply(reply, 8, fb));
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 1.234f, fb.position);
  TEST_ASSERT_FLOAT_WITHIN(5e-3f, -2.5f, fb.velocity);
  TEST_ASSERT_FLOAT_WITHIN(0.05f, 10.0f, fb.torque);
}

static void test_error_strings() {
  TEST_ASSERT_EQUAL_STRING("нет ошибок", ak80::error_to_string(0));
  TEST_ASSERT_EQUAL_STRING("блокировка вала", ak80::error_to_string(7));
  TEST_ASSERT_EQUAL_STRING("неизвестный код", ak80::error_to_string(200));
}

static int run_all() {
  UNITY_BEGIN();
  RUN_TEST(test_float_to_uint_endpoints_and_clamping);
  RUN_TEST(test_roundtrip_all_fields);
  RUN_TEST(test_pack_command_layout_min_max);
  RUN_TEST(test_pack_command_layout_max_min);
  RUN_TEST(test_pack_command_clamps_out_of_range);
  RUN_TEST(test_zero_command_gives_near_zero_torque);
  RUN_TEST(test_special_frames);
  RUN_TEST(test_unpack_full_reply);
  RUN_TEST(test_unpack_midscale_is_near_zero);
  RUN_TEST(test_unpack_short_reply_without_diag);
  RUN_TEST(test_unpack_rejects_truncated_frame);
  RUN_TEST(test_command_survives_roundtrip_through_reply_decoder);
  RUN_TEST(test_error_strings);
  return UNITY_END();
}

#ifdef ARDUINO
void setup() {
  delay(2000);  // Mega перезагружается при открытии порта
  run_all();
}
void loop() {}
#else
int main() { return run_all(); }
#endif
