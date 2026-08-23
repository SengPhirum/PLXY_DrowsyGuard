# The camera, the speaker, the card and the browser. No panel: board_display.*
# and display_ui.* were removed when the preview moved into the web server.
idf_component_register(SRCS "main.cpp" "risk_filter.cpp" "model_adapter.cpp"
                            "voice_alert.cpp" "voice_clips.cpp" "behavior.cpp"
                            "board_audio.cpp" "board_wifi.cpp" "board_sdcard.cpp"
                            "web_server.cpp" "eye_model.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES esp_http_server esp_wifi esp_netif nvs_flash
                                esp_timer driver fatfs
                                esp_driver_sdmmc sdmmc
                       # EMBED_TXTFILES for the page, because the text form appends
                       # a NUL and that is what lets web_server.cpp compute its
                       # length as (end - start - 1). EMBED_FILES for the audio,
                       # because a WAV is binary and a NUL would corrupt it.
                       #
                       # Both languages are embedded, so the board speaks with no SD
                       # card in it. The card still wins when it has a clip - that is
                       # how a hand recording replaces a synthesised one without a
                       # rebuild. ~690 kB of the 6 MB app partition.
                       EMBED_TXTFILES "web/index.html"
                       EMBED_FILES "../assets/audio/en_drowsy.wav"
                                   "../assets/audio/en_microsleep.wav"
                                   "../assets/audio/en_yawning.wav"
                                   "../assets/audio/en_head_nod.wav"
                                   "../assets/audio/km_drowsy.wav"
                                   "../assets/audio/km_microsleep.wav"
                                   "../assets/audio/km_yawning.wav"
                                   "../assets/audio/km_head_nod.wav")
