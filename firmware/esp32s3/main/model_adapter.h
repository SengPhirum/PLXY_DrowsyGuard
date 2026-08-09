#pragma once
#include <stdint.h>
#include <stddef.h>

bool model_init();
float model_predict_drowsy(const uint8_t* gray64x64, size_t len);
