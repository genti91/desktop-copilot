#pragma once

// Configuracion del ST7789 de 240x240 para LovyanGFX.
//
// Con TFT_eSPI esto vivia en build_flags; LovyanGFX lo pide como una clase, que
// ademas deja explicito que bus y que host SPI se usan en vez de deducirlos de
// macros.
//
// La luz de fondo NO se declara aca a proposito: la maneja setDisplayPower() en
// display.cpp, que la apaga y prende segun la configuracion remota. Si LovyanGFX
// tambien la controlara, se estarian peleando por el mismo pin.

#include <LovyanGFX.hpp>

#include "device_config.h"

class Panel240 : public lgfx::LGFX_Device {
 public:
  Panel240() {
    {
      auto cfg = bus_.config();
      // SPI2 es el unico host de proposito general libre en el S3: SPI0 y SPI1
      // los usan la flash y la PSRAM.
      cfg.spi_host = SPI2_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = 40000000;  // por la matriz GPIO; 80 MHz solo via IOMUX
      cfg.freq_read = 16000000;
      cfg.spi_3wire = true;   // sin MISO
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = PIN_TFT_SCLK;
      cfg.pin_mosi = PIN_TFT_MOSI;
      cfg.pin_miso = -1;
      cfg.pin_dc = PIN_TFT_DC;
      bus_.config(cfg);
      panel_.setBus(&bus_);
    }

    {
      auto cfg = panel_.config();
      cfg.pin_cs = -1;  // atado a masa en el modulo
      cfg.pin_rst = PIN_TFT_RST;
      cfg.pin_busy = -1;
      cfg.panel_width = SCREEN_W;
      cfg.panel_height = SCREEN_H;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.readable = false;  // sin MISO no se puede leer de vuelta
      cfg.invert = true;     // los 240x240 de esta familia van invertidos
      cfg.rgb_order = false;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      panel_.config(cfg);
    }

    setPanel(&panel_);
  }

 private:
  lgfx::Panel_ST7789 panel_;
  lgfx::Bus_SPI bus_;
};
