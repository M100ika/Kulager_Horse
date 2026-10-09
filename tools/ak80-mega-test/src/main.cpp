// Интерактивная консоль AK80-64: Arduino Mega + MCP2515, MIT-режим.
// Команды приходят строками через USB (115200 бод), список выводит help.
// Прошивка и терминал: pio run -e mega -t upload -t monitor
//
// Пока мотор в режиме управления, каждые kCommandPeriodMs уходит команда и
// приходит ответ с состоянием. Из ответов копится статистика связи и ходов.
//
// Защита:
// - уставка не отходит от вала настолько, чтобы момент при заклинивании
//   превысил maxt (kp * отставание + kd * скорость уставки <= maxt);
// - позиция в пределах ±kSoftLimitRad, вращение тормозит перед пределом;
// - при ошибке мотора, перегреве или потере связи вал освобождается;
// - после перезагрузки Mega мотор выводится из режима управления.
//
// Пины, кварц, CAN ID и стартовые kp, kd, maxt берутся из test_config.h.

#include <Arduino.h>
#include <SPI.h>
#include <ctype.h>
#include <math.h>
#include <mcp2515.h>
#include <stdlib.h>
#include <string.h>

#include "ak80_mit.h"
#include "test_config.h"

constexpr float kDeg = PI / 180.0;  // радиан в градусе
constexpr uint32_t kPeriodUs = kCommandPeriodMs * 1000UL;
constexpr uint16_t kReplyTimeoutUs = 3000;
constexpr uint8_t kLinkLostMisses = 10;        // подряд без ответа, 50 мс
constexpr float kSoftLimitRad = 12.0f;         // диапазон MIT ±12,5 рад
constexpr float kMaxSpeedRad = 300.0f * kDeg;  // рад/с
constexpr float kMaxAccelRad = 1000.0f * kDeg; // рад/с²
constexpr uint8_t kStallCycles = 100;          // 0,5 с вал не догоняет уставку
constexpr float kMinMoveS = 0.2f;              // самый короткий ход
constexpr uint8_t kLineLen = 48;

static const ak80::Command kLimp = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};

enum class Mode : uint8_t { kOff, kLimp, kHold, kMove, kVel };
static const char kModeCode[] = "OLHMV";  // для телеметрии, по порядку Mode

struct Settings {
  float speed;       // наибольшая скорость ходов, рад/с
  float accel;       // наибольшее ускорение ходов и вращения, рад/с²
  float kp;          // Н·м/рад
  float kd;          // Н·м·с/рад
  float max_torque;  // Н·м
};

// Текущий ход: косинусный профиль скорости, затем дотягивание к цели.
struct Move {
  float from, to;
  uint32_t start_ms;
  uint32_t duration_ms;
  bool settling;
  uint32_t settle_ms;
  float lag_max;  // наибольшее |уставка - позиция| за ход
  float torque_peak;
};

struct Cycle {
  uint16_t legs;  // всего ходов, 0: цикла нет
  uint16_t leg;   // номер текущего хода, с 1
  float a, b;     // концы: нечётные ходы идут в b, чётные в a
};

struct MoveStats {
  uint16_t n;
  float err_mean;  // ошибка в конце хода со знаком, рад
  float err_m2;    // сумма квадратов отклонений для СКО (метод Уэлфорда)
  float err_abs_max;
  float lag_max;
  float torque_peak;
};

struct Stats {
  uint32_t sent, replies;
  uint32_t rtt_sum_us;
  uint16_t rtt_max_us;
  uint16_t miss_streak, miss_streak_max;
  float torque_abs_max, speed_abs_max;
  int16_t temp_max;
  uint32_t clamps;  // срабатывания ограничения момента (циклов управления)
  uint16_t faults;  // аварийные остановки
  MoveStats moves;
};

static MCP2515 can(kCanCsPin);
static bool can_ok = false;
static Mode mode = Mode::kOff;
static Settings cfg = {90.0f * kDeg, 180.0f * kDeg, kMoveKp, kMoveKd,
                       kMoveMaxTorque};
static ak80::Feedback fb;
static bool have_fb = false;
static bool link_lost = false;
static float p_ref = 0.0f;     // уставка позиции, рад
static float v_ref = 0.0f;     // уставка скорости в vel, рад/с
static float v_target = 0.0f;  // заданная скорость в vel, рад/с
static Move mv;
static Cycle cyc;
static MoveStats cyc_stats;
static Stats st;
static uint8_t stall_cycles = 0;  // подряд циклов с ограничением момента
static uint16_t stream_period_ms = 0;
static uint32_t stream_last_ms = 0;
static uint32_t last_tick_us = 0;
static char line[kLineLen];
static uint8_t line_len = 0;

static float max_f(float a, float b) { return a > b ? a : b; }
static float clamp_f(float x, float lo, float hi) {
  return x < lo ? lo : (x > hi ? hi : x);
}

// ---------- CAN ----------

static void drain_rx() {
  struct can_frame f;
  while (can.readMessage(&f) == MCP2515::ERROR_OK) {
  }
}

static bool send_frame(const uint8_t data[ak80::kFrameLen]) {
  struct can_frame tx;
  tx.can_id = kMotorId;
  tx.can_dlc = ak80::kFrameLen;
  memcpy(tx.data, data, ak80::kFrameLen);
  return can.sendMessage(&tx) == MCP2515::ERROR_OK;
}

// Отправляет кадр и ждёт ответ мотора. Ответ попадает в fb.
static bool transact(const uint8_t data[ak80::kFrameLen]) {
  drain_rx();
  st.sent++;
  if (!send_frame(data)) return false;

  const uint32_t t0 = micros();
  struct can_frame rx;
  ak80::Feedback reply;
  while (micros() - t0 < kReplyTimeoutUs) {
    if (can.readMessage(&rx) != MCP2515::ERROR_OK) continue;
    if (rx.can_id & (CAN_EFF_FLAG | CAN_RTR_FLAG)) continue;
    if (!ak80::unpack_reply(rx.data, rx.can_dlc, reply)) continue;
    if (reply.motor_id != kMotorId) continue;

    const uint16_t rtt = micros() - t0;
    st.replies++;
    st.rtt_sum_us += rtt;
    if (rtt > st.rtt_max_us) st.rtt_max_us = rtt;
    fb = reply;
    have_fb = true;
    return true;
  }
  return false;
}

static bool send_command(const ak80::Command& cmd) {
  uint8_t buf[ak80::kFrameLen];
  ak80::pack_command(cmd, buf);
  return transact(buf);
}

static bool send_special(void (*make)(uint8_t*)) {
  uint8_t buf[ak80::kFrameLen];
  make(buf);
  return transact(buf);
}

// ---------- Вывод ----------

static void print_deg(float rad, uint8_t digits = 1) {
  Serial.print(rad / kDeg, digits);
  Serial.print(F("°"));
}

static const __FlashStringHelper* mode_name(Mode m) {
  switch (m) {
    case Mode::kOff: return F("не в режиме управления");
    case Mode::kLimp: return F("вал свободен");
    case Mode::kHold: return F("удержание");
    case Mode::kMove: return F("ход");
    case Mode::kVel: return F("вращение");
  }
  return F("?");
}

static void print_help() {
  Serial.println(F(
      "Команды (углы в градусах выходного вала, скорость в °/с):\n"
      "  m <угол>         ход в позицию (move)\n"
      "  r <угол>         поворот от текущей уставки (rel)\n"
      "  v <скорость>     вращение; v 0 плавно останавливает (vel)\n"
      "  c <угол> <N>     N раз туда и обратно, статистика по ходам (cycle)\n"
      "  hold             держать текущую позицию\n"
      "  x                освободить вал, нулевой момент (stop)\n"
      "  on / off         войти в режим управления / выйти из него\n"
      "  speed <°/с>      наибольшая скорость ходов\n"
      "  accel <°/с²>     наибольшее ускорение ходов и вращения\n"
      "  kp <Н·м/рад>     жёсткость\n"
      "  kd <Н·м·с/рад>   демпфирование\n"
      "  maxt <Н·м>       предел момента при заклинивании\n"
      "  zero             текущая позиция становится нулём (вал свободен)\n"
      "  s                состояние мотора (status)\n"
      "  stats            статистика; stats reset сбрасывает\n"
      "  stream <Гц>      телеметрия CSV; stream 0 выключает\n"
      "  help             эта справка\n"
      "Команды движения сами включают мотор. Остановить: x и Enter."));
}

static void print_settings() {
  Serial.print(F("Настройки: speed "));
  Serial.print(cfg.speed / kDeg, 0);
  Serial.print(F(" °/с, accel "));
  Serial.print(cfg.accel / kDeg, 0);
  Serial.print(F(" °/с², kp "));
  Serial.print(cfg.kp, 1);
  Serial.print(F(", kd "));
  Serial.print(cfg.kd, 2);
  Serial.print(F(", maxt "));
  Serial.print(cfg.max_torque, 1);
  Serial.print(F(" Н·м, предел позиции ±"));
  print_deg(kSoftLimitRad, 0);
  Serial.println();
}

static void print_feedback() {
  Serial.print(F("Позиция "));
  print_deg(fb.position);
  Serial.print(F(", скорость "));
  Serial.print(fb.velocity / kDeg, 1);
  Serial.print(F(" °/с, момент "));
  Serial.print(fb.torque, 2);
  Serial.print(F(" Н·м"));
  if (fb.has_diag) {
    Serial.print(F(", "));
    Serial.print(fb.temperature_c);
    Serial.print(F(" °C, "));
    Serial.print(ak80::error_to_string(fb.error));
  }
  Serial.println();
}

static void print_status() {
  Serial.print(F("Режим: "));
  Serial.print(mode_name(mode));
  if (mode == Mode::kHold || mode == Mode::kMove || mode == Mode::kVel) {
    Serial.print(F(", уставка "));
    print_deg(p_ref);
  }
  if (link_lost) Serial.print(F(", СВЯЗИ НЕТ"));
  Serial.println();
  if (have_fb) {
    if (mode == Mode::kOff) Serial.print(F("Последний ответ. "));
    print_feedback();
  } else {
    Serial.println(F("Ответов от мотора ещё не было: on включает мотор"));
  }
  print_settings();
}

static void print_move_stats(const MoveStats& s) {
  Serial.print(F("ходов "));
  Serial.print(s.n);
  if (s.n == 0) {
    Serial.println();
    return;
  }
  Serial.print(F(", ошибка в конце: средняя "));
  print_deg(s.err_mean, 2);
  Serial.print(F(", СКО "));
  print_deg(s.n > 1 ? sqrt(s.err_m2 / (s.n - 1)) : 0.0f, 2);
  Serial.print(F(", наибольшая "));
  print_deg(s.err_abs_max, 2);
  Serial.print(F("; отставание до "));
  print_deg(s.lag_max);
  Serial.print(F(", пик момента "));
  Serial.print(s.torque_peak, 2);
  Serial.println(F(" Н·м"));
}

static void print_stats() {
  Serial.print(F("Связь: команд "));
  Serial.print(st.sent);
  Serial.print(F(", ответов "));
  Serial.print(st.replies);
  if (st.sent > 0) {
    Serial.print(F(" (потери "));
    Serial.print(100.0f * (st.sent - st.replies) / st.sent, 2);
    Serial.print(F(" %)"));
  }
  if (st.replies > 0) {
    Serial.print(F(", задержка средняя "));
    Serial.print(st.rtt_sum_us / st.replies);
    Serial.print(F(" мкс, наибольшая "));
    Serial.print(st.rtt_max_us);
    Serial.print(F(" мкс"));
  }
  Serial.print(F(", подряд без ответа до "));
  Serial.println(st.miss_streak_max);

  Serial.print(F("Ходы: "));
  print_move_stats(st.moves);

  Serial.print(F("Наибольшие: момент "));
  Serial.print(st.torque_abs_max, 2);
  Serial.print(F(" Н·м, скорость "));
  Serial.print(st.speed_abs_max / kDeg, 1);
  Serial.print(F(" °/с, температура "));
  if (st.temp_max == INT16_MIN) {
    Serial.print(F("нет данных"));
  } else {
    Serial.print(st.temp_max);
    Serial.print(F(" °C"));
  }
  Serial.println();

  Serial.print(F("Ограничение момента: "));
  Serial.print(st.clamps);
  Serial.print(F(" циклов, аварийных остановок "));
  Serial.println(st.faults);
}

static void reset_stats() {
  st = Stats();
  st.temp_max = INT16_MIN;
}

static void add_move(MoveStats& s, float err) {
  s.n++;
  const float d = err - s.err_mean;
  s.err_mean += d / s.n;
  s.err_m2 += d * (err - s.err_mean);
  s.err_abs_max = max_f(s.err_abs_max, fabsf(err));
  s.lag_max = max_f(s.lag_max, mv.lag_max);
  s.torque_peak = max_f(s.torque_peak, mv.torque_peak);
}

// ---------- Режимы ----------

static void cancel_cycle() { cyc.legs = 0; }

static void go_limp() {
  cancel_cycle();
  stall_cycles = 0;
  if (mode != Mode::kOff) mode = Mode::kLimp;
}

static void fault(const __FlashStringHelper* reason) {
  go_limp();
  st.faults++;
  Serial.print(F("! Остановка, вал свободен: "));
  Serial.println(reason);
}

static bool motor_on() {
  if (mode != Mode::kOff) return true;
  if (!can_ok) {
    Serial.println(F("CAN-контроллер не инициализирован, см. README"));
    return false;
  }
  for (uint8_t i = 0; i < 3; i++) {
    if (send_special(ak80::make_enter_motor_mode)) {
      mode = Mode::kLimp;
      link_lost = false;
      st.miss_streak = 0;
      p_ref = fb.position;
      Serial.println(F("Мотор в режиме управления, вал свободен"));
      return true;
    }
  }
  Serial.println(F("Мотор не отвечает: питание, kMotorId, test_bus_scan"));
  return false;
}

static void motor_off() {
  if (mode == Mode::kOff) return;
  cancel_cycle();
  send_command(kLimp);
  send_special(ak80::make_exit_motor_mode);
  mode = Mode::kOff;
  Serial.println(F("Мотор вышел из режима управления"));
}

// Включает мотор при необходимости. Из свободного вала уставка берётся
// с текущей позиции, иначе остаётся прежней, чтобы вал не дёрнулся.
static bool begin_position_control() {
  if (!motor_on()) return false;
  if (mode == Mode::kLimp) p_ref = fb.position;
  return true;
}

static bool target_ok(float target) {
  if (fabsf(target) <= kSoftLimitRad) return true;
  Serial.print(F("Цель "));
  print_deg(target);
  Serial.print(F(" вне диапазона ±"));
  print_deg(kSoftLimitRad, 0);
  Serial.println();
  return false;
}

static void start_move(float to) {
  mv.from = p_ref;
  mv.to = to;
  // Косинусный профиль на ходе d за время T: пиковая скорость пи*d/(2T),
  // пиковое ускорение пи²*d/(2T²). T выбирается по более жёсткому пределу.
  const float d = fabsf(to - p_ref);
  const float duration_s =
      max_f(kMinMoveS, max_f(PI * d / (2.0f * cfg.speed),
                             PI * sqrt(d / (2.0f * cfg.accel))));
  mv.duration_ms = duration_s * 1000.0f;
  mv.start_ms = millis();
  mv.settling = false;
  mv.lag_max = 0.0f;
  mv.torque_peak = 0.0f;
  mode = Mode::kMove;
}

static void print_leg(float err) {
  Serial.print(F("Ход"));
  if (cyc.legs) {
    Serial.print(' ');
    Serial.print(cyc.leg);
    Serial.print('/');
    Serial.print(cyc.legs);
  }
  Serial.print(F(": "));
  print_deg(mv.from);
  Serial.print(F(" → "));
  print_deg(mv.to);
  Serial.print(F(" за "));
  Serial.print(mv.duration_ms / 1000.0f, 2);
  Serial.print(F(" с, ошибка "));
  print_deg(err, 2);
  Serial.print(F(", отставание до "));
  print_deg(mv.lag_max);
  Serial.print(F(", пик момента "));
  Serial.print(mv.torque_peak, 2);
  Serial.println(F(" Н·м"));
}

static void finish_move() {
  const float err = fb.position - mv.to;
  add_move(st.moves, err);
  if (cyc.legs) add_move(cyc_stats, err);
  print_leg(err);

  mode = Mode::kHold;
  p_ref = mv.to;
  if (!cyc.legs) return;
  if (cyc.leg < cyc.legs) {
    cyc.leg++;
    start_move(cyc.leg % 2 ? cyc.b : cyc.a);
  } else {
    Serial.print(F("Цикл завершён: "));
    print_move_stats(cyc_stats);
    cancel_cycle();
  }
}

static void update_move(ak80::Command& cmd) {
  const uint32_t t = millis() - mv.start_ms;
  if (t < mv.duration_ms) {
    const float phase = PI * t / mv.duration_ms;
    const float d = mv.to - mv.from;
    p_ref = mv.from + d * (0.5f - 0.5f * cosf(phase));
    cmd.v_des = d * 0.5f * PI / (mv.duration_ms / 1000.0f) * sinf(phase);
  } else if (!mv.settling) {
    mv.settling = true;
    mv.settle_ms = millis();
    p_ref = mv.to;
  } else if (millis() - mv.settle_ms >= kMoveHoldMs) {
    finish_move();
  }
  cmd.p_des = p_ref;
}

// Уставка движется с заданной скоростью, разгон и торможение плавные.
// Перед пределом позиции вращение тормозит само.
static void update_vel(float dt, ak80::Command& cmd) {
  const float brake = v_ref * v_ref / (2.0f * cfg.accel);
  if (v_target != 0.0f &&
      ((v_ref > 0.0f && p_ref + brake >= kSoftLimitRad) ||
       (v_ref < 0.0f && p_ref - brake <= -kSoftLimitRad))) {
    v_target = 0.0f;
    Serial.println(F("Предел позиции, торможение"));
  }
  const float dv = cfg.accel * dt;
  v_ref += clamp_f(v_target - v_ref, -dv, dv);
  p_ref = clamp_f(p_ref + v_ref * dt, -kSoftLimitRad, kSoftLimitRad);
  cmd.p_des = p_ref;
  cmd.v_des = v_ref;
}

// Момент при заклиненном вале: kp * (p_des - p) + kd * v_des. Если он может
// превысить maxt, уставка подтягивается к валу, а v_des обнуляется.
static void limit_torque(ak80::Command& cmd) {
  const float e = cmd.p_des - fb.position;
  if (cfg.kp * fabsf(e) + cfg.kd * fabsf(cmd.v_des) <= cfg.max_torque) {
    stall_cycles = 0;
    return;
  }
  const float lead = cfg.max_torque / cfg.kp;
  if (fabsf(e) > lead) cmd.p_des = fb.position + (e > 0.0f ? lead : -lead);
  cmd.v_des = 0.0f;
  st.clamps++;
  if (mode == Mode::kVel) p_ref = cmd.p_des;  // не копить уставку впереди вала
  if (++stall_cycles >= kStallCycles) {
    stall_cycles = 0;
    fault(F("вал не догоняет уставку: заклинен, перегружен или мотор не в "
            "режиме управления"));
    cmd = kLimp;
  }
}

static void check_reply(bool ok) {
  if (!ok) {
    st.miss_streak++;
    if (st.miss_streak > st.miss_streak_max) st.miss_streak_max = st.miss_streak;
    if (st.miss_streak == kLinkLostMisses) {
      link_lost = true;
      fault(F("нет ответа от мотора. Если повторяется при разгоне, "
              "проверьте ЛБП: ограничение тока и просадку напряжения"));
    }
    return;
  }
  st.miss_streak = 0;
  if (link_lost) {
    // Мотор мог перезагрузиться и выйти из режима управления: возвращаем,
    // вал остаётся свободным.
    link_lost = false;
    const bool armed = send_special(ak80::make_enter_motor_mode);
    Serial.println(armed ? F("Связь восстановлена, мотор снова в режиме "
                             "управления, вал свободен")
                         : F("Связь восстановлена, но вход в режим управления "
                             "не подтверждён: on"));
  }

  const float torque = fabsf(fb.torque);
  st.torque_abs_max = max_f(st.torque_abs_max, torque);
  st.speed_abs_max = max_f(st.speed_abs_max, fabsf(fb.velocity));
  if (fb.has_diag && fb.temperature_c > st.temp_max) {
    st.temp_max = fb.temperature_c;
  }
  if (mode == Mode::kMove) {
    mv.lag_max = max_f(mv.lag_max, fabsf(p_ref - fb.position));
    mv.torque_peak = max_f(mv.torque_peak, torque);
  }

  if (mode == Mode::kLimp || !fb.has_diag) return;
  if (fb.error != 0) {
    Serial.print(F("! Ошибка мотора: "));
    Serial.println(ak80::error_to_string(fb.error));
    fault(F("ошибка мотора"));
  } else if (fb.temperature_c >= kMaxTemperatureC) {
    fault(F("перегрев драйвера"));
  }
}

static void control_tick(float dt) {
  ak80::Command cmd = {p_ref, 0.0f, cfg.kp, cfg.kd, 0.0f};
  switch (mode) {
    case Mode::kOff:
      return;
    case Mode::kLimp:
      cmd = kLimp;
      break;
    case Mode::kHold:
      break;
    case Mode::kMove:
      update_move(cmd);
      break;
    case Mode::kVel:
      update_vel(dt, cmd);
      break;
  }
  if (mode != Mode::kLimp) limit_torque(cmd);
  check_reply(send_command(cmd));
}

// Печатает и в режиме O (мотор не опрашивается): значения тогда последние
// известные, зато пульт на ПК всегда видит режим.
static void stream_tick() {
  if (stream_period_ms == 0) return;
  const uint32_t now = millis();
  if (now - stream_last_ms < stream_period_ms) return;
  stream_last_ms = now;
  Serial.print(now);
  Serial.print(',');
  Serial.print(kModeCode[static_cast<uint8_t>(mode)]);
  Serial.print(',');
  Serial.print(p_ref / kDeg, 2);
  Serial.print(',');
  Serial.print(fb.position / kDeg, 2);
  Serial.print(',');
  Serial.print(fb.velocity / kDeg, 1);
  Serial.print(',');
  Serial.print(fb.torque, 2);
  Serial.print(',');
  Serial.print(fb.temperature_c);
  Serial.print(',');
  Serial.println(fb.error);
}

// ---------- Команды ----------

static bool is(const char* a, const char* b) { return strcmp(a, b) == 0; }

static bool parse_float(const char* s, float& out) {
  if (s == nullptr) return false;
  char* end;
  out = strtod(s, &end);
  return end != s && *end == '\0';
}

static bool arg_in_range(const char* s, float lo, float hi, float& out) {
  if (parse_float(s, out) && out >= lo && out <= hi) return true;
  Serial.print(F("Нужно число от "));
  Serial.print(lo, 1);
  Serial.print(F(" до "));
  Serial.println(hi, 1);
  return false;
}

static void handle_line(char* s) {
  for (char* c = s; *c; c++) {
    *c = *c == ',' ? '.' : tolower(static_cast<unsigned char>(*c));
  }
  const char* cmd = strtok(s, " \t");
  if (cmd == nullptr) return;
  const char* a1 = strtok(nullptr, " \t");
  const char* a2 = strtok(nullptr, " \t");
  const float limit_deg = kSoftLimitRad / kDeg;
  const float speed_deg = kMaxSpeedRad / kDeg;
  float x, y;

  if (is(cmd, "help") || is(cmd, "?")) {
    print_help();
    print_settings();
  } else if (is(cmd, "s") || is(cmd, "status")) {
    print_status();
  } else if (is(cmd, "x") || is(cmd, "stop")) {
    go_limp();
    Serial.println(F("Вал свободен"));
  } else if (is(cmd, "on")) {
    motor_on();
  } else if (is(cmd, "off")) {
    motor_off();
  } else if (is(cmd, "hold")) {
    if (motor_on()) {
      cancel_cycle();
      p_ref = fb.position;
      mode = Mode::kHold;
      Serial.print(F("Удержание в "));
      print_deg(p_ref);
      Serial.println();
    }
  } else if (is(cmd, "m") || is(cmd, "move")) {
    if (arg_in_range(a1, -limit_deg, limit_deg, x) && begin_position_control()) {
      cancel_cycle();
      start_move(x * kDeg);
    }
  } else if (is(cmd, "r") || is(cmd, "rel")) {
    if (arg_in_range(a1, -2 * limit_deg, 2 * limit_deg, x) &&
        begin_position_control() && target_ok(p_ref + x * kDeg)) {
      cancel_cycle();
      start_move(p_ref + x * kDeg);
    }
  } else if (is(cmd, "v") || is(cmd, "vel")) {
    if (arg_in_range(a1, -speed_deg, speed_deg, x) && begin_position_control()) {
      cancel_cycle();
      if (mode != Mode::kVel) v_ref = 0.0f;
      v_target = x * kDeg;
      mode = Mode::kVel;
    }
  } else if (is(cmd, "c") || is(cmd, "cycle")) {
    if (arg_in_range(a1, -2 * limit_deg, 2 * limit_deg, x) &&
        arg_in_range(a2, 1, 1000, y) && begin_position_control() &&
        target_ok(p_ref + x * kDeg)) {
      cyc.legs = 2 * static_cast<uint16_t>(y);
      cyc.leg = 1;
      cyc.a = p_ref;
      cyc.b = p_ref + x * kDeg;
      cyc_stats = MoveStats();
      start_move(cyc.b);
    }
  } else if (is(cmd, "accel")) {
    if (arg_in_range(a1, 10, kMaxAccelRad / kDeg, x)) {
      cfg.accel = x * kDeg;
      print_settings();
    }
  } else if (is(cmd, "speed")) {
    if (arg_in_range(a1, 1, speed_deg, x)) {
      cfg.speed = x * kDeg;
      print_settings();
    }
  } else if (is(cmd, "kp")) {
    if (arg_in_range(a1, 1, 200, x)) {
      cfg.kp = x;
      print_settings();
    }
  } else if (is(cmd, "kd")) {
    if (arg_in_range(a1, 0, 5, x)) {
      cfg.kd = x;
      print_settings();
    }
  } else if (is(cmd, "maxt")) {
    if (arg_in_range(a1, 1, 60, x)) {
      cfg.max_torque = x;
      print_settings();
    }
  } else if (is(cmd, "zero")) {
    if (mode != Mode::kOff && mode != Mode::kLimp) {
      Serial.println(F("Сначала освободите вал: x"));
    } else if (can_ok && send_special(ak80::make_set_zero)) {
      p_ref = fb.position;
      Serial.print(F("Ноль задан. "));
      print_feedback();
    } else {
      Serial.println(F("Мотор не ответил"));
    }
  } else if (is(cmd, "stats")) {
    if (a1 != nullptr && is(a1, "reset")) {
      reset_stats();
      Serial.println(F("Статистика сброшена"));
    } else {
      print_stats();
    }
  } else if (is(cmd, "stream")) {
    if (arg_in_range(a1, 0, 100, x)) {
      stream_period_ms = x > 0.0f ? static_cast<uint16_t>(1000.0f / x) : 0;
      if (stream_period_ms) {
        Serial.println(F("t_ms,mode,p_ref_deg,pos_deg,vel_dps,torque_nm,"
                         "temp_c,error"));
      } else {
        Serial.println(F("Телеметрия выключена"));
      }
    }
  } else {
    Serial.println(F("Неизвестная команда, список: help"));
  }
}

static void read_serial() {
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\n' || c == '\r') {
      line[line_len] = '\0';
      if (line_len) handle_line(line);
      line_len = 0;
    } else if (line_len < kLineLen - 1) {
      line[line_len++] = c;
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.print(F("\nAK80-64, консоль стенда. CAN ID "));
  Serial.print(kMotorId);
  Serial.print(F(", кварц MCP2515 "));
  Serial.print(kMcpCrystalMhz);
  Serial.println(F(" МГц"));

  const CAN_CLOCK clock = kMcpCrystalMhz == 8 ? MCP_8MHZ : MCP_16MHZ;
  can_ok = can.reset() == MCP2515::ERROR_OK &&
           can.setBitrate(CAN_1000KBPS, clock) == MCP2515::ERROR_OK &&
           can.setNormalMode() == MCP2515::ERROR_OK;
  if (can_ok) {
    // После перезагрузки Mega мотор мог остаться в режиме управления
    // и выполнять последнюю команду.
    send_command(kLimp);
    send_special(ak80::make_exit_motor_mode);
  } else {
    Serial.println(F("MCP2515 не отвечает по SPI, см. README"));
  }

  reset_stats();
  print_help();
  print_settings();
  last_tick_us = micros();
}

void loop() {
  read_serial();
  const uint32_t now = micros();
  if (now - last_tick_us < kPeriodUs) return;
  const float dt = clamp_f((now - last_tick_us) * 1e-6f, 0.0f, 0.02f);
  last_tick_us = now;
  control_tick(dt);
  stream_tick();
}
