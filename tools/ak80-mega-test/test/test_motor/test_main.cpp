// Стендовая проверка AK80-64: Arduino Mega + MCP2515 + мотор в MIT-режиме.
// Запуск: pio test -e mega -f test_motor -v
//
// Порядок: CAN-контроллер → ответ мотора → состояние в покое → устойчивость
// связи → пробное движение → выход из режима управления.
// После каждого теста, в том числе упавшего, мотор получает нулевой момент.

#include <Arduino.h>
#include <SPI.h>
#include <math.h>
#include <mcp2515.h>
#include <string.h>
#include <unity.h>

#include "ak80_mit.h"
#include "test_config.h"

// Наибольшее отставание вала от уставки: при нём пружинная часть момента
// kMoveKp * kMaxLeadRad равна kMoveMaxTorque.
constexpr float kMaxLeadRad = kMoveMaxTorque / kMoveKp;
static_assert(kMaxLeadRad > 2 * kMoveToleranceRad,
              "kMoveMaxTorque / kMoveKp слишком мало: вал не дойдёт до уставки");

static const ak80::Command kLimp = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};

static MCP2515 can(kCanCsPin);
static bool can_ready = false;
static bool motor_active = false;     // мотор в режиме управления
static uint16_t ext_frames_seen = 0;  // расширенные кадры = признак Servo-режима

// ---------- CAN ----------

static void drain_rx() {
  struct can_frame f;
  while (can.readMessage(&f) == MCP2515::ERROR_OK) {
    if (f.can_id & CAN_EFF_FLAG) ext_frames_seen++;
  }
}

static bool send_frame(const uint8_t data[ak80::kFrameLen]) {
  struct can_frame tx;
  tx.can_id = kMotorId;  // стандартный кадр, ID = ID мотора
  tx.can_dlc = ak80::kFrameLen;
  memcpy(tx.data, data, ak80::kFrameLen);
  return can.sendMessage(&tx) == MCP2515::ERROR_OK;
}

// Отправляет кадр и ждёт ответ этого мотора. Чужие кадры пропускает.
static bool transact(const uint8_t data[ak80::kFrameLen], ak80::Feedback& fb) {
  drain_rx();
  if (!send_frame(data)) return false;

  const uint32_t start = millis();
  struct can_frame rx;
  while (millis() - start < kReplyTimeoutMs) {
    if (can.readMessage(&rx) != MCP2515::ERROR_OK) continue;
    if (rx.can_id & CAN_EFF_FLAG) {
      ext_frames_seen++;
      continue;
    }
    if (rx.can_id & CAN_RTR_FLAG) continue;
    if (ak80::unpack_reply(rx.data, rx.can_dlc, fb) && fb.motor_id == kMotorId) {
      return true;
    }
  }
  return false;
}

static bool command(const ak80::Command& cmd, ak80::Feedback& fb) {
  uint8_t buf[ak80::kFrameLen];
  ak80::pack_command(cmd, buf);
  return transact(buf, fb);
}

static void go_limp() {
  ak80::Feedback fb;
  for (uint8_t i = 0; i < 3; i++) {
    if (command(kLimp, fb)) return;
  }
}

// ---------- Вывод ----------

static void log_feedback(const char* label, const ak80::Feedback& fb) {
  char p[12], v[12], t[12], line[192];
  dtostrf(fb.position, 1, 3, p);
  dtostrf(fb.velocity, 1, 3, v);
  dtostrf(fb.torque, 1, 2, t);
  if (fb.has_diag) {
    snprintf(line, sizeof(line),
             "%s: позиция %s рад, скорость %s рад/с, момент %s Н·м, %d °C, "
             "ошибка %u (%s)",
             label, p, v, t, fb.temperature_c, static_cast<unsigned>(fb.error),
             ak80::error_to_string(fb.error));
  } else {
    snprintf(line, sizeof(line),
             "%s: позиция %s рад, скорость %s рад/с, момент %s Н·м", label, p,
             v, t);
  }
  TEST_MESSAGE(line);
}

// Читает регистр MCP2515 напрямую, в обход библиотеки, на SPI 1 МГц.
// Если на 1 МГц значения верные, а библиотека (8 МГц) не работает,
// виноваты длинные или плохие провода SPI.
static uint8_t read_mcp_register(uint8_t addr) {
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  digitalWrite(kCanCsPin, LOW);
  SPI.transfer(0x03);  // инструкция READ
  SPI.transfer(addr);
  const uint8_t value = SPI.transfer(0x00);
  digitalWrite(kCanCsPin, HIGH);
  SPI.endTransaction();
  return value;
}

static void log_spi_state() {
  // После сброса MCP2515 в режиме конфигурации: CANSTAT 0x80, CANCTRL 0x87.
  // 0xFF: модуль не отвечает (CS, SO, питание); 0x00: SO прижат к земле.
  char line[96];
  snprintf(line, sizeof(line),
           "SPI 1 МГц: CANSTAT=0x%02X (норма 0x80), CANCTRL=0x%02X (норма 0x87)",
           read_mcp_register(0x0E), read_mcp_register(0x0F));
  TEST_MESSAGE(line);
}

static void log_bus_state() {
  char line[96];
  snprintf(line, sizeof(line), "Ошибки CAN: TEC=%u, REC=%u, EFLG=0x%02X",
           can.errorCountTX(), can.errorCountRX(), can.getErrorFlags());
  TEST_MESSAGE(line);
}

// ---------- Движение ----------

// Плавно (косинусный профиль) ведёт вал из from в to и держит позицию.
// Уставка не уходит от вала дальше kMaxLeadRad: если вал заклинит, момент
// останется в пределах kMoveMaxTorque при любом ходе.
// false, если связь потеряна. На входе в fb ответ мотора с текущей позицией,
// на выходе последний ответ.
static bool move(float from, float to, ak80::Feedback& fb, float& peak_torque) {
  const float ramp_s = kMoveRampMs / 1000.0f;
  const uint32_t t0 = millis();
  uint8_t misses = 0;
  for (;;) {
    const uint32_t t = millis() - t0;
    if (t > static_cast<uint32_t>(kMoveRampMs) + kMoveHoldMs) return true;

    ak80::Command cmd = {to, 0.0f, kMoveKp, kMoveKd, 0.0f};
    if (t < kMoveRampMs) {
      const float phase = PI * t / kMoveRampMs;
      cmd.p_des = from + (to - from) * (0.5f - 0.5f * cosf(phase));
      cmd.v_des = (to - from) * 0.5f * PI / ramp_s * sinf(phase);
    }
    if (fabsf(cmd.p_des - fb.position) > kMaxLeadRad) {
      cmd.p_des = fb.position +
                  (cmd.p_des > fb.position ? kMaxLeadRad : -kMaxLeadRad);
      cmd.v_des = 0.0f;
    }

    if (command(cmd, fb)) {
      misses = 0;
      if (fabsf(fb.torque) > peak_torque) peak_torque = fabsf(fb.torque);
    } else if (++misses >= 5) {
      return false;
    }
    delay(kCommandPeriodMs);
  }
}

// ---------- Тесты ----------

void setUp() {}

void tearDown() {
  if (motor_active) go_limp();
}

static void test_can_controller_init() {
  TEST_ASSERT_TRUE_MESSAGE(kMcpCrystalMhz == 8 || kMcpCrystalMhz == 16,
                           "kMcpCrystalMhz должен быть 8 или 16");
  if (can.reset() != MCP2515::ERROR_OK) {
    log_spi_state();
    TEST_FAIL_MESSAGE(
        "MCP2515 не отвечает по SPI: проверьте питание 5 В и CS=53, SI=51, "
        "SO=50, SCK=52");
  }
  const CAN_CLOCK clock = kMcpCrystalMhz == 8 ? MCP_8MHZ : MCP_16MHZ;
  TEST_ASSERT_EQUAL_MESSAGE(MCP2515::ERROR_OK,
                            can.setBitrate(CAN_1000KBPS, clock),
                            "не удалось задать 1 Мбит/с");
  TEST_ASSERT_EQUAL_MESSAGE(MCP2515::ERROR_OK, can.setNormalMode(),
                            "MCP2515 не перешёл в нормальный режим");
  can_ready = true;
  if (kMcpCrystalMhz == 8) {
    TEST_MESSAGE("ВНИМАНИЕ: кварц 8 МГц, 1 Мбит/с вне спецификации MCP2515");
  }
}

static void test_motor_responds() {
  if (!can_ready) TEST_IGNORE_MESSAGE("CAN-контроллер не инициализирован");

  uint8_t enter[ak80::kFrameLen];
  ak80::make_enter_motor_mode(enter);
  ak80::Feedback fb;
  bool ok = false;
  for (uint8_t attempt = 0; attempt < 3 && !ok; attempt++) {
    ok = transact(enter, fb);
  }

  if (!ok) {
    log_bus_state();
    if (ext_frames_seen > 0) {
      TEST_FAIL_MESSAGE(
          "Мотор не отвечает в MIT-режиме, но на шине есть расширенные кадры: "
          "похоже, он в Servo-режиме. Переключите в MIT через CubeMars Upper "
          "Computer");
    } else {
      TEST_FAIL_MESSAGE(
          "Нет ответа от мотора. Если TEC=0 и REC=0, мотор на шине, но ID "
          "другой: найдите его тестом test_bus_scan. Иначе см. таблицу TEC/REC "
          "в README");
    }
  }

  motor_active = true;
  log_feedback("Мотор на связи", fb);
}

static void test_rest_state() {
  if (!motor_active) TEST_IGNORE_MESSAGE("мотор не на связи");

  ak80::Feedback fb;
  TEST_ASSERT_TRUE_MESSAGE(command(kLimp, fb), "нет ответа на нулевую команду");
  log_feedback("В покое", fb);

  TEST_ASSERT_FLOAT_WITHIN_MESSAGE(kRestMaxVelocity, 0.0f, fb.velocity,
                                   "вал вращается без команды");
  if (!fb.has_diag) {
    TEST_MESSAGE("прошивка не передаёт температуру и код ошибки");
    return;
  }
  TEST_ASSERT_EQUAL_UINT8_MESSAGE(0, fb.error, ak80::error_to_string(fb.error));
  TEST_ASSERT_LESS_THAN_INT16_MESSAGE(kMaxTemperatureC, fb.temperature_c,
                                      "перегрев драйвера");
}

static void test_link_stability() {
  if (!motor_active) TEST_IGNORE_MESSAGE("мотор не на связи");

  uint16_t replies = 0;
  uint32_t rtt_sum_us = 0, rtt_max_us = 0;
  ak80::Feedback fb;
  for (uint16_t i = 0; i < kLinkTestFrames; i++) {
    const uint32_t t0 = micros();
    if (command(kLimp, fb)) {
      const uint32_t rtt = micros() - t0;
      replies++;
      rtt_sum_us += rtt;
      if (rtt > rtt_max_us) rtt_max_us = rtt;
    }
    delay(kCommandPeriodMs);
  }

  char line[128];
  snprintf(line, sizeof(line),
           "Связь: %u ответов из %u, задержка средняя %lu мкс, макс. %lu мкс",
           replies, kLinkTestFrames,
           replies ? rtt_sum_us / replies : 0UL, rtt_max_us);
  TEST_MESSAGE(line);
  if (replies < kLinkTestFrames) log_bus_state();

  TEST_ASSERT_TRUE_MESSAGE(replies >= kLinkMinSuccess * kLinkTestFrames,
                           "слишком много потерянных ответов");
}

static void test_position_move() {
  if (!motor_active) TEST_IGNORE_MESSAGE("мотор не на связи");
  if (kMoveRad <= 0.0f) TEST_IGNORE_MESSAGE("движение отключено (kMoveRad = 0)");

  ak80::Feedback fb;
  TEST_ASSERT_TRUE_MESSAGE(command(kLimp, fb), "нет ответа от мотора");
  const float start = fb.position;
  // Движение к нулю, чтобы не выйти за диапазон ±12.5 рад.
  const float target = start > 0.0f ? start - kMoveRad : start + kMoveRad;

  float peak_torque = 0.0f;
  bool ok = move(start, target, fb, peak_torque);
  log_feedback("После перехода", fb);
  TEST_ASSERT_TRUE_MESSAGE(ok, "потеряна связь во время движения");
  TEST_ASSERT_FLOAT_WITHIN_MESSAGE(kMoveToleranceRad, target, fb.position,
                                   "вал не пришёл в заданную позицию");

  ok = move(target, start, fb, peak_torque);
  log_feedback("После возврата", fb);

  char t[12], line[64];
  dtostrf(peak_torque, 1, 2, t);
  snprintf(line, sizeof(line), "Пиковый момент: %s Н·м", t);
  TEST_MESSAGE(line);

  TEST_ASSERT_TRUE_MESSAGE(ok, "потеряна связь во время движения");
  TEST_ASSERT_FLOAT_WITHIN_MESSAGE(kMoveToleranceRad, start, fb.position,
                                   "вал не вернулся в стартовую позицию");
  if (fb.has_diag) {
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(0, fb.error,
                                    ak80::error_to_string(fb.error));
  }
}

static void test_exit_motor_mode() {
  if (!motor_active) TEST_IGNORE_MESSAGE("мотор не на связи");

  go_limp();
  uint8_t exit_frame[ak80::kFrameLen];
  ak80::make_exit_motor_mode(exit_frame);
  const bool sent = send_frame(exit_frame);
  motor_active = false;
  TEST_ASSERT_TRUE_MESSAGE(sent, "кадр выхода из режима управления не отправлен");
}

void setup() {
  delay(2000);  // Mega перезагружается при открытии порта
  UNITY_BEGIN();
  RUN_TEST(test_can_controller_init);
  RUN_TEST(test_motor_responds);
  RUN_TEST(test_rest_state);
  RUN_TEST(test_link_stability);
  RUN_TEST(test_position_move);
  RUN_TEST(test_exit_motor_mode);
  UNITY_END();
}

void loop() {}
