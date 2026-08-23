# The camera, the speaker and the browser. No panel: board_display.* and
# display_ui.* were removed when the preview moved into the web server.
idf_component_register(SRCS "main.cpp" "risk_filter.cpp" "model_adapter.cpp"
                            "voice_alert.cpp" "behavior.cpp" "board_audio.cpp"
                            "board_wifi.cpp" "web_server.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES esp_http_server esp_wifi esp_netif nvs_flash
                                esp_timer driver
                       # EMBED_TXTFILES, not EMBED_FILES: the text form appends a
                       # NUL, which is what lets web_server.cpp compute the page
                       # length as (end - start - 1).
                       EMBED_TXTFILES "web/index.html")
