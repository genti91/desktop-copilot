cmake_minimum_required(VERSION 3.16.0)

# MicroLink vive como submodulo en third_party/. Se apunta a los dos componentes
# por su ruta real en vez de a third_party/microlink/components, porque ese
# directorio trae wireguard_lwip como symlink y en Windows git lo checkoutea
# como un archivo de texto que CMake no resuelve.
#
# list(APPEND ...) y no set(): PlatformIO ya pasa EXTRA_COMPONENT_DIRS por linea
# de comandos con el directorio de src/ y el de Arduino, y pisarlo rompe el build.
list(APPEND EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/third_party/microlink/components/microlink"
    "${CMAKE_CURRENT_LIST_DIR}/third_party/microlink/components/microlink/components/wireguard_lwip"
)

# El componente de Arduino toma su nombre de la carpeta del paquete que instala
# PlatformIO (hoy framework-arduinoespressif32), no se llama "arduino". Se saca
# de la lista para no hardcodear como se llame el paquete manana.
set(ARDUINO_COMPONENT "framework-arduinoespressif32")
foreach(component_dir IN LISTS EXTRA_COMPONENT_DIRS)
    if(component_dir MATCHES "arduinoespressif32")
        get_filename_component(ARDUINO_COMPONENT "${component_dir}" NAME)
    endif()
endforeach()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(desktop-copilot)
