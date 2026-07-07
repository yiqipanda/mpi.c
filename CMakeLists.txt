cmake_minimum_required(VERSION 3.20)
#Says this project needs at least CMake 3.20.

project(mpi_simulator LANGUAGES C CXX)
#Declares the project name as mpi_simulator
#Tells CMake this project uses both C and C++.

option(DEBUG "Enable verbose simulator logging" ON)
#Defines a CMake option named DEBUG.
#The text in quotes is the description shown to users.
#ON means debug logging is enabled by default unless someone turns it off when configuring.

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
#Requests the C11 standard for any C code in the project.
#Makes C11 mandatory.
#Requests the C++17 standard for any C++ code in the project.
#Makes C++17 mandatory.

find_package(Threads REQUIRED)
#Asks CMake to find the system threading library support.
#REQUIRED means configuration fails if threading support is not found.
#This is typically used because your code or dependencies likely use std::thread or pthreads.

add_library(mpi_sim STATIC
    src/mpi_sim.cpp
    src/trace.cpp
)
#Creates a library target named mpi_sim
#STATIC means it builds as a static library, not a shared library.
#src/mpi_sim.cpp is the source file used to build that library.

target_include_directories(mpi_sim PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
#Adds the include directory to the header search path for mpi_sim.
#${CMAKE_CURRENT_SOURCE_DIR} is the directory containing the current CMakeLists.txt.
#Anything that links against mpi_sim also inherits that include path.

target_link_libraries(mpi_sim PUBLIC Threads::Threads)
#Links the threading library into mpi_sim.
#Threads::Threads is CMake’s imported target for thread support.
#PUBLIC means anything that links to mpi_sim also gets the thread dependency information.

target_compile_definitions(mpi_sim PUBLIC $<$<BOOL:${DEBUG}>:MPI_SIM_DEBUG=1>)
#$<$<BOOL:${DEBUG}>:...> means “only apply this if DEBUG is true.”
#f DEBUG is ON, code compiled for mpi_sim and anything that links to it will see MPI_SIM_DEBUG=1.
#That usually lets the C++ code enable extra logging or debug behavior with #ifdef or #if

add_executable(mpi_sim_demo main.cpp)
#Creates an executable target named mpi_sim_demo.
#built from main.cpp

target_link_libraries(mpi_sim_demo PRIVATE mpi_sim)
#Links the executable against the mpi_sim library.
#PRIVATE means only mpi_sim_demo directly depends on mpi_sim.

install(TARGETS mpi_sim_demo RUNTIME DESTINATION bin)
#Defines install rules for the executable.
#RUNTIME refers to executable files on platforms like Windows and Unix.
#When someone runs the install step, mpi_sim_demo will be copied into bin.
