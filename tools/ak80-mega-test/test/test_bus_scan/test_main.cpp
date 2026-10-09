// Поиск мотора на шине, когда test_motor пишет «нет ответа от мотора».
// Запуск: pio test -e mega -f test_bus_scan -v
//
// Сначала слушает шину, ничего не отправляя: мотор в Servo-режиме сам шлёт
// расширенные кадры с состоянием, в MIT-режиме молчит. Затем шлёт вход в режим
// управления на каждый ID от 1 до kScanMaxId и печатает все ответы. Ответившему
// мотору сразу уходят нулевой момент и выход из режима: вал не двигается.

#include <Arduino.h>
#include <SPI.h>
#include <mcp2515.h>
#include <string.h>
#include <unity.h>

#include "ak80_mit.h"
#include "test_config.h"

constexpr uint16_t kListenMs = 1000;
constexpr uint8_t kScanMaxId = 127;
constexpr uint8_t kMaxLoggedFrames = 10;

static const ak80::Command kLimp = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};

static MCP2515 can(kCanCsPin);
static bool can_ready = false;

// ---------- CAN ----------

static void drain_rx() {
  struct can_frame f;
  while (can.readMessage(&f) == MCP2515::ERROR_OK) {
  }
}

static bool send_std(uint16_t id, const uint8_t data[ak80::kFrameLen]) {
  struct can_frame tx;
  tx.can_id = id;
  tx.can_dlc = ak80::kFrameLen;
  memcpy(tx.data, data, ak80::kFrameLen);
  return can.sendMessage(&tx) == MCP2515::ERROR_OK;
}

// ---------- Вывод ----------

static void log_frame(const char* label, const struct can_frame& f) {
  const bool ext = f.can_id & CAN_EFF_FLAG;
  const unsigned long id = f.can_id & (ext ? CAN_EFF_MASK : CAN_SFF_MASK);
  // Буферы с запасом: кириллица в UTF-8 занимает 2 байта на букву.
  char line[160];
  size_t n = snprintf(line, sizeof(line), "%s: %s ID 0x%lX, DLC %u, данные",
                      label, ext ? "расширенный" : "стандартный", id, f.can_dlc);
  for (uint8_t i = 0; i < f.can_dlc && i < 8 && n + 4 < sizeof(line); i++) {
    n += snprintf(line + n, sizeof(line) - n, " %02X", f.data[i]);
  }
  TEST_MESSAGE(line);
}

static void log_bus_state() {
  char line[96];
  snprintf(line, sizeof(line), "Ошибки CAN: TEC=%u, REC=%u, EFLG=0x%02X",
           can.errorCountTX(), can.errorCountRX(), can.getErrorFlags());
  TEST_MESSAGE(line);
}

// ---------- Тесты ----------

void setUp() {}
void tearDown() {}

static void test_can_controller_init() {
  TEST_ASSERT_EQUAL_MESSAGE(MCP2515::ERROR_OK, can.reset(),
                            "MCP2515 не отвечает по SPI");
  const CAN_CLOCK clock = kMcpCrystalMhz == 8 ? MCP_8MHZ : MCP_16MHZ;
  TEST_ASSERT_EQUAL_MESSAGE(MCP2515::ERROR_OK,
                            can.setBitrate(CAN_1000KBPS, clock),
                            "не удалось задать 1 Мбит/с");
  TEST_ASSERT_EQUAL_MESSAGE(MCP2515::ERROR_OK, can.setNormalMode(),
                            "MCP2515 не перешёл в нормальный режим");
  can_ready = true;
}

static void test_listen() {
  if (!can_ready) TEST_IGNORE_MESSAGE("CAN-контроллер не инициализирован");

  uint16_t std_frames = 0, ext_frames = 0;
  struct can_frame f;
  const uint32_t start = millis();
  while (millis() - start < kListenMs) {
    if (can.readMessage(&f) != MCP2515::ERROR_OK) continue;
    if (f.can_id & CAN_EFF_FLAG) {
      ext_frames++;
    } else {
      std_frames++;
    }
    if (std_frames + ext_frames <= kMaxLoggedFrames) log_frame("Кадр", f);
  }

  char line[128];
  snprintf(line, sizeof(line),
           "Прослушивание %u мс: стандартных кадров %u, расширенных %u",
           kListenMs, std_frames, ext_frames);
  TEST_MESSAGE(line);
  log_bus_state();
  if (ext_frames > 0) {
    TEST_MESSAGE(
        "Расширенные кадры: мотор, похоже, в Servo-режиме, его ID в младшем "
        "байте ID кадра");
  }
}

static void test_mit_scan() {
  if (!can_ready) TEST_IGNORE_MESSAGE("CAN-контроллер не инициализирован");

  uint8_t enter[ak80::kFrameLen], limp[ak80::kFrameLen],
      exit_frame[ak80::kFrameLen];
  ak80::make_enter_motor_mode(enter);
  ak80::pack_command(kLimp, limp);
  ak80::make_exit_motor_mode(exit_frame);

  uint8_t found = 0;
  uint8_t send_failures = 0;
  for (uint8_t id = 1; id <= kScanMaxId; id++) {
    drain_rx();
    if (!send_std(id, enter)) {
      send_failures++;
      continue;
    }

    bool answered = false;
    struct can_frame f;
    const uint32_t start = millis();
    while (millis() - start < kReplyTimeoutMs) {
      if (can.readMessage(&f) != MCP2515::ERROR_OK) continue;
      char label[32];
      snprintf(label, sizeof(label), "Ответ на ID %u", id);
      log_frame(label, f);
      answered = true;
    }
    if (answered) {
      found++;
      send_std(id, limp);
      delay(2);
      send_std(id, exit_frame);
      delay(kReplyTimeoutMs);
    }
  }

  char line[128];
  snprintf(line, sizeof(line),
           "Опрошены ID 1..%u: ответили %u, не отправлено кадров %u",
           kScanMaxId, found, send_failures);
  TEST_MESSAGE(line);
  log_bus_state();
  TEST_ASSERT_TRUE_MESSAGE(
      found > 0,
      "Ни один ID не ответил в MIT-режиме. Если кадры подтверждаются (TEC=0), "
      "мотор на шине, но, вероятно, в Servo-режиме: проверьте в CubeMars "
      "Upper Computer");
}

void setup() {
  delay(2000);  // Mega перезагружается при открытии порта
  UNITY_BEGIN();
  RUN_TEST(test_can_controller_init);
  RUN_TEST(test_listen);
  RUN_TEST(test_mit_scan);
  UNITY_END();
}

void loop() {}
