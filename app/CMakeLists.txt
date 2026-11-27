# Qt 6 Widgets application (phase 0: skeleton). Executable: adv-tg-qt (adv-tg stays the 2.x
# Python application until the 3.0 release).
find_package(Qt6 6.5 REQUIRED COMPONENTS Widgets PrintSupport Network)
qt_standard_project_setup()

qt_add_executable(advtg_app src/main.cpp)
# Console executable while it is a skeleton (--version / --smoke-test print to stdout); the
# .app bundle and the Windows GUI subsystem come with the packaging (phase 5).
set_target_properties(advtg_app PROPERTIES
  WIN32_EXECUTABLE FALSE
  MACOSX_BUNDLE FALSE
  OUTPUT_NAME adv-tg-qt
  CXX_STANDARD 20
  CXX_STANDARD_REQUIRED ON)
target_compile_definitions(advtg_app PRIVATE ADVTG_APP_VERSION="${PROJECT_VERSION}-dev")
target_link_libraries(advtg_app PRIVATE Qt6::Widgets advtg_native)
if(MSVC)
  target_compile_options(advtg_app PRIVATE /W4 /utf-8 /permissive-)
else()
  target_compile_options(advtg_app PRIVATE -Wall -Wextra)
endif()
