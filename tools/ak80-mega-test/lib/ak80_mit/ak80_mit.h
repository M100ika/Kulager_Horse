#pragma once

// Протокол MIT-режима CubeMars AK80-64.
// Источник: AK Series Module Driver User Manual V1.0.15, раздел 5.3.
//
// Команда: стандартный CAN-кадр, ID = ID мотора (заводской 1), DLC 8,
//   позиция 16 бит | скорость 12 | Kp 12 | Kd 12 | момент 12.
// Ответ: data[0] = ID мотора, позиция 16 | скорость 12 | момент 12,
//   data[6] = температура + 40, data[7] = код ошибки.
// Шина: 1 Мбит/с.
//
// Здесь только кодирование и декодирование кадров, без CAN-драйвера:
// код собирается и на ПК (unit-тесты), и на микроконтроллере.

#include <stdint.h>

namespace ak80 {

struct Limits {
  float p_min, p_max;    // позиция выходного вала, рад
  float v_min, v_max;    // скорость, рад/с
  float t_min, t_max;    // момент, Н·м
  float kp_min, kp_max;  // Н·м/рад
  float kd_min, kd_max;  // Н·м·с/рад
};

// Диапазоны AK80-64 из таблицы мануала.
constexpr Limits kAK80_64 = {-12.5f, 12.5f, -8.0f, 8.0f, -144.0f, 144.0f,
                             0.0f,   500.0f, 0.0f, 5.0f};

constexpr uint8_t kFrameLen = 8;

// Мотор отрабатывает момент: kp * (p_des - p) + kd * (v_des - v) + t_ff.
struct Command {
  float p_des;
  float v_des;
  float kp;
  float kd;
  float t_ff;
};

struct Feedback {
  uint8_t motor_id;
  float position;  // рад
  float velocity;  // рад/с
  float torque;    // Н·м; масштаб по диапазону момента, проверить на стенде
  bool has_diag;   // в ответе есть температура и код ошибки (DLC 8)
  int16_t temperature_c;
  uint8_t error;
};

uint16_t float_to_uint(float x, float x_min, float x_max, uint8_t bits);
float uint_to_float(uint16_t x, float x_min, float x_max, uint8_t bits);

void pack_command(const Command& cmd, uint8_t out[kFrameLen],
                  const Limits& lim = kAK80_64);
bool unpack_reply(const uint8_t* data, uint8_t len, Feedback& fb,
                  const Limits& lim = kAK80_64);

// Служебные кадры. Вход в режим управления заодно возвращает текущее состояние.
void make_enter_motor_mode(uint8_t out[kFrameLen]);
void make_exit_motor_mode(uint8_t out[kFrameLen]);
void make_set_zero(uint8_t out[kFrameLen]);

const char* error_to_string(uint8_t code);

}  // namespace ak80
