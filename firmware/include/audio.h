#pragma once

#include <Arduino.h>
#include "AudioGeneratorMP3.h"
#include "AudioOutputI2S.h"
#include "AudioFileSourceLittleFS.h"

extern uint8_t* pcm_buffer;
extern AudioGeneratorMP3* mp3;
extern AudioFileSourceLittleFS* file;
extern AudioOutputI2S* out;
extern uint32_t playbackStartedMs;

void initAudio();
void recordWhileTouched();
void updateAudioPlayback();
