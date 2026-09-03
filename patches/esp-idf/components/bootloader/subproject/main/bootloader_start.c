/*
 * SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdbool.h>
#include <stdint.h>
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "bootloader_init.h"
#include "bootloader_utility.h"
#include "bootloader_common.h"
#include "bootloader_hooks.h"
#include "nvs.h"
#include "nvs_bootloader.h"

static const char *TAG = "boot";

// Namespace/keys the app writes at runtime (before entering deep sleep) to control
// how this bootloader behaves on wake-up/reset. Absent keys keep the previous
// unconditional "always try launcher" behavior.
#define LAUNCHER_NVS_PARTITION      "nvs"
#define LAUNCHER_NVS_NAMESPACE      "launcher"
#define LAUNCHER_NVS_KEY_DDLB       "DDLB"           // bool: Disable Deepsleep Launcher Boot
#define LAUNCHER_NVS_KEY_ON_KEY     "LauncherOnKey"  // int32: GPIO number, -1 = disabled
#define LAUNCHER_NVS_KEY_ON_KEY_LVL "LauncherKeyLvl" // bool: GPIO level that triggers the launcher

typedef struct {
    bool ddlb;               // Disable Deepsleep Launcher Boot
    int32_t on_key_gpio;     // -1 = not configured
    bool on_key_level;
} launcher_nvs_config_t;

static int select_partition_number(bootloader_state_t *bs);
static int selected_boot_partition(const bootloader_state_t *bs);
static bool should_try_launcher_test_partition(int reset_reason);
static void load_launcher_nvs_config(launcher_nvs_config_t *cfg);
static bool launcher_key_pressed(const launcher_nvs_config_t *cfg);

/*
 * We arrive here after the ROM bootloader finished loading this second stage bootloader from flash.
 * The hardware is mostly uninitialized, flash cache is down and the app CPU is in reset.
 * We do have a stack, so we can do the initialization in C.
 */
void __attribute__((noreturn)) call_start_cpu0(void)
{
    // (0. Call the before-init hook, if available)
    if (bootloader_before_init) {
        bootloader_before_init();
    }

    // 1. Hardware initialization
    if (bootloader_init() != ESP_OK) {
        bootloader_reset();
    }

    // (1.1 Call the after-init hook, if available)
    if (bootloader_after_init) {
        bootloader_after_init();
    }

#ifdef CONFIG_BOOTLOADER_SKIP_VALIDATE_IN_DEEP_SLEEP
    // If this boot is a wake up from the deep sleep then go to the short way,
    // try to load the application which worked before deep sleep.
    // It skips a lot of checks due to it was done before (while first boot).
    bootloader_utility_load_boot_image_from_deep_sleep();
    // If it is not successful try to load an application as usual.
#endif

    // 2. Select the number of boot partition
    bootloader_state_t bs = {0};
    int boot_index = select_partition_number(&bs);
    if (boot_index == INVALID_INDEX) {
        bootloader_reset();
    }

    // 2.1 Load the TEE image
#if CONFIG_SECURE_ENABLE_TEE
    bootloader_utility_load_tee_image(&bs);
#endif

    // 3. Load the app image for booting
    bootloader_utility_load_boot_image(&bs, boot_index);
}

// Select the number of boot partition
static int select_partition_number(bootloader_state_t *bs)
{
    // 1. Load partition table
    if (!bootloader_utility_load_partition_table(bs)) {
        ESP_LOGE(TAG, "load partition table error!");
        return INVALID_INDEX;
    }

    // 2. Select the number of boot partition
    return selected_boot_partition(bs);
}

/*
 * Selects a boot partition.
 * The conditions for switching to another firmware are checked.
 */
static int selected_boot_partition(const bootloader_state_t *bs)
{
    int boot_index = bootloader_utility_get_selected_boot_partition(bs);
    if (boot_index == INVALID_INDEX) {
        return boot_index; // Unrecoverable failure.
    }

    // components/soc/esp32p4/include/soc/reset_reasons.h
    int reset_reason = esp_rom_get_reset_reason(0);
    esp_rom_printf("[%s] Turned on because (1= POWERON_RESET or 5==ESP_RST_DEEPSLEEP) --> %d\n", TAG, esp_rom_get_reset_reason(0));
    if (should_try_launcher_test_partition(reset_reason)) {
        if (bs->test.offset != 0) {
            return TEST_APP_INDEX;
        }
        if (bs->factory.offset != 0) {
            return FACTORY_INDEX;
        }
    }

    return boot_index;
}

static bool should_try_launcher_test_partition(int reset_reason)
{
    launcher_nvs_config_t cfg = {
        .ddlb = false,
        .on_key_gpio = -1,
        .on_key_level = false,
    };
    load_launcher_nvs_config(&cfg);

    // A physical key held at the configured level always forces the launcher,
    // regardless of reset reason, so a device stuck in aggressive deep sleep
    // can still be recovered by holding a button while resetting it.
    if(cfg.on_key_gpio > 0) {
        if (launcher_key_pressed(&cfg)) {
            esp_rom_printf("[%s] LauncherOnKey (GPIO%d==%d) pressed -> forcing launcher\n", TAG, (int)cfg.on_key_gpio, (int)cfg.on_key_level);
            return true;
        }
        return false;
    }

    if (reset_reason == RESET_REASON_CHIP_POWER_ON) {
        return true;
    }

    // DDLB: when set, only a real power-on (or the key above) may enter the launcher;
    // wake-ups from the app's own deep sleep go straight back to the selected app.
    if (cfg.ddlb) {
        return false;
    }

    if (reset_reason == RESET_REASON_CORE_DEEP_SLEEP) {
        return true;
    }
#if defined(CONFIG_IDF_TARGET_ESP32P4)
    if (reset_reason == RESET_REASON_CORE_MWDT) {
        return true;
    }
#endif

    return false;
}

// Reads the "launcher" namespace from NVS. Any failure (partition/namespace/key not
// found, NVS not yet initialized by the app, etc.) leaves `cfg` at its caller-supplied
// defaults, preserving the historical "always try launcher" behavior.
static void load_launcher_nvs_config(launcher_nvs_config_t *cfg)
{
    nvs_bootloader_read_list_t read_list[] = {
        {
            .namespace_name = LAUNCHER_NVS_NAMESPACE,
            .key_name = LAUNCHER_NVS_KEY_DDLB,
            .value_type = NVS_TYPE_U8,
        },
        {
            .namespace_name = LAUNCHER_NVS_NAMESPACE,
            .key_name = LAUNCHER_NVS_KEY_ON_KEY,
            .value_type = NVS_TYPE_I32,
        },
        {
            .namespace_name = LAUNCHER_NVS_NAMESPACE,
            .key_name = LAUNCHER_NVS_KEY_ON_KEY_LVL,
            .value_type = NVS_TYPE_U8,
        },
    };

    esp_err_t err = nvs_bootloader_read(LAUNCHER_NVS_PARTITION, sizeof(read_list) / sizeof(read_list[0]), read_list);
    if (err != ESP_OK) {
        esp_rom_printf("[%s] launcher NVS config unavailable (err=0x%x), using defaults\n", TAG, err);
        return;
    }

    if (read_list[0].result_code == ESP_OK) {
        cfg->ddlb = (read_list[0].value.u8_val != 0);
    }
    if (read_list[1].result_code == ESP_OK) {
        cfg->on_key_gpio = read_list[1].value.i32_val;
    }
    if (read_list[2].result_code == ESP_OK) {
        cfg->on_key_level = (read_list[2].value.u8_val != 0);
    }
}

// Instant (non-blocking) sample of the configured GPIO: true only if it currently
// reads at `on_key_level`. Uses delay_sec = 0 so it never stalls the boot path.
static bool launcher_key_pressed(const launcher_nvs_config_t *cfg)
{
    if (cfg->on_key_gpio < 0) {
        return false;
    }

    esp_comm_gpio_hold_t hold = bootloader_common_check_long_hold_gpio_level((uint32_t)cfg->on_key_gpio, 0, cfg->on_key_level);
    return hold != GPIO_NOT_HOLD;
}

#if CONFIG_LIBC_NEWLIB
// Return global reent struct if any newlib functions are linked to bootloader
struct _reent *__getreent(void)
{
    return _GLOBAL_REENT;
}
#endif
