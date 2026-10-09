#include "ak80_mit.h"

namespace ak80 {

namespace {

float clamp(float x, float lo, float hi) {
  return x < lo ? lo : (x > hi ? hi : x);
}

// 32-битный сдвиг: на AVR int 16-битный, и 1 << 16 там равно нулю.
uint16_t max_int(uint8_t bits) {
  return static_cast<uint16_t>((1UL << bits) - 1);
}

void make_special(uint8_t out[kFrameLen], uint8_t last) {
  for (uint8_t i = 0; i < kFrameLen - 1; i++) out[i] = 0xFF;
  out[kFrameLen - 1] = last;
}

}  // namespace

// В примере из мануала шкала (1 << bits) / span: x_max даёт 2^bits, поле
// переполняется, и P_MAX превращается в P_MIN. Здесь шкала (2^bits - 1) / span,
// как в обратном преобразовании, плюс округление до ближайшего.
uint16_t float_to_uint(float x, float x_min, float x_max, uint8_t bits) {
  const float span = x_max - x_min;
  x = clamp(x, x_min, x_max);
  return static_cast<uint16_t>((x - x_min) * max_int(bits) / span + 0.5f);
}

float uint_to_float(uint16_t x, float x_min, float x_max, uint8_t bits) {
  return static_cast<float>(x) * (x_max - x_min) / max_int(bits) + x_min;
}

void pack_command(const Command& cmd, uint8_t out[kFrameLen],
                  const Limits& lim) {
  const uint16_t p = float_to_uint(cmd.p_des, lim.p_min, lim.p_max, 16);
  const uint16_t v = float_to_uint(cmd.v_des, lim.v_min, lim.v_max, 12);
  const uint16_t kp = float_to_uint(cmd.kp, lim.kp_min, lim.kp_max, 12);
  const uint16_t kd = float_to_uint(cmd.kd, lim.kd_min, lim.kd_max, 12);
  const uint16_t t = float_to_uint(cmd.t_ff, lim.t_min, lim.t_max, 12);

  out[0] = static_cast<uint8_t>(p >> 8);
  out[1] = static_cast<uint8_t>(p & 0xFF);
  out[2] = static_cast<uint8_t>(v >> 4);
  out[3] = static_cast<uint8_t>(((v & 0xF) << 4) | (kp >> 8));
  out[4] = static_cast<uint8_t>(kp & 0xFF);
  out[5] = static_cast<uint8_t>(kd >> 4);
  out[6] = static_cast<uint8_t>(((kd & 0xF) << 4) | (t >> 8));
  out[7] = static_cast<uint8_t>(t & 0xFF);
}

// Старые прошивки отвечают 6 байтами (без температуры и кода ошибки).
bool unpack_reply(const uint8_t* data, uint8_t len, Feedback& fb,
                  const Limits& lim) {
  if (len < 6) return false;

  const uint16_t p = (static_cast<uint16_t>(data[1]) << 8) | data[2];
  const uint16_t v = (static_cast<uint16_t>(data[3]) << 4) | (data[4] >> 4);
  const uint16_t t = (static_cast<uint16_t>(data[4] & 0xF) << 8) | data[5];

  fb.motor_id = data[0];
  fb.position = uint_to_float(p, lim.p_min, lim.p_max, 16);
  fb.velocity = uint_to_float(v, lim.v_min, lim.v_max, 12);
  fb.torque = uint_to_float(t, lim.t_min, lim.t_max, 12);
  fb.has_diag = len >= 8;
  fb.temperature_c = fb.has_diag ? static_cast<int16_t>(data[6]) - 40 : 0;
  fb.error = fb.has_diag ? data[7] : 0;
  return true;
}

void make_enter_motor_mode(uint8_t out[kFrameLen]) { make_special(out, 0xFC); }
void make_exit_motor_mode(uint8_t out[kFrameLen]) { make_special(out, 0xFD); }
void make_set_zero(uint8_t out[kFrameLen]) { make_special(out, 0xFE); }

const char* error_to_string(uint8_t code) {
  switch (code) {
    case 0: return "нет ошибок";
    case 1: return "перегрев мотора";
    case 2: return "перегрузка по току";
    case 3: return "перенапряжение";
    case 4: return "недонапряжение";
    case 5: return "ошибка энкодера";
    case 6: return "перегрев MOSFET";
    case 7: return "блокировка вала";
    default: return "неизвестный код";
  }
}

}  // namespace ak80
