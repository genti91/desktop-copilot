FILE(GLOB_RECURSE app_sources ${CMAKE_SOURCE_DIR}/src/*.c*)

# El directorio se llama src y no main, asi que no hereda la dependencia
# automatica contra todos los componentes: hay que declarar lo que se usa.
# ARDUINO_COMPONENT lo resuelve el CMakeLists de la raiz.
idf_component_register(
    SRCS ${app_sources}
    INCLUDE_DIRS "." "../include"
    REQUIRES ${ARDUINO_COMPONENT} microlink
)
