#pragma once

// Настройки стендового теста. Перед первым запуском сверьте с вашим стендом.

#include <stdint.h>

// ---- MCP2515 ----
// SPI на Mega фиксирован: MOSI 51, MISO 50, SCK 52. Здесь только CS.
constexpr uint8_t kCanCsPin = 53;

// Частота кварца на модуле MCP2515 (написана на корпусе кварца): 8 или 16.
// AK80-64 работает только на 1 Мбит/с. С кварцем 8 МГц эта скорость выходит
// за спецификацию MCP2515 и работает ненадёжно, нужен кварц 16 МГц.
constexpr uint8_t kMcpCrystalMhz = 8;

// ---- Мотор ----
constexpr uint8_t kMotorId = 4;  // CAN ID мотора (заводское 1; найти: test_bus_scan)

// ---- Связь ----
constexpr uint16_t kReplyTimeoutMs = 20;  // ожидание ответа на один кадр
constexpr uint8_t kCommandPeriodMs = 5;   // период команд, 200 Гц
constexpr uint16_t kLinkTestFrames = 400;  // кадров в проверке связи (2 с)
constexpr float kLinkMinSuccess = 0.98f;  // минимальная доля ответов

// ---- Состояние в покое ----
constexpr float kRestMaxVelocity = 0.3f;  // рад/с
constexpr int16_t kMaxTemperatureC = 70;

// ---- Пробное движение ----
// Вал плавно уходит на kMoveRad от стартовой позиции и возвращается.
// Вал должен быть свободен: без повода и нагрузки, мотор закреплён на стенде.
// 0 отключает тест движения.
constexpr float kMoveRad = 3.1416f;      // рад на выходном валу (180°)
constexpr float kMoveKp = 30.0f;         // Н·м/рад
constexpr float kMoveKd = 1.0f;          // Н·м·с/рад
// Пиковая скорость перехода: kMoveRad * π / (2 * время), здесь ~1,6 рад/с.
constexpr uint16_t kMoveRampMs = 3000;   // время плавного перехода
constexpr uint16_t kMoveHoldMs = 500;    // удержание перед проверкой
constexpr float kMoveToleranceRad = 0.05f;
// Верхняя граница момента при заклиненном вале. Уставка не уходит от вала
// дальше kMoveMaxTorque / kMoveKp, поэтому граница не зависит от kMoveRad.
constexpr float kMoveMaxTorque = 15.0f;  // Н·м
