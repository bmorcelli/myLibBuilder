idf_component_register(SRCS "bootloader_start.c"
                    REQUIRES bootloader bootloader_support
                    PRIV_REQUIRES nvs_flash)

idf_build_get_property(scripts BOOTLOADER_LINKER_SCRIPT)
target_linker_script(${COMPONENT_LIB} INTERFACE "${scripts}")

target_link_libraries(${COMPONENT_LIB} INTERFACE "-u bootloader_hooks_include")
