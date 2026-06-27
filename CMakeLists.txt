cmake_minimum_required(VERSION 3.20)

project(mpi_simulator LANGUAGES C CXX)

option(DEBUG "Enable verbose simulator logging" ON)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Threads REQUIRED)

add_library(mpi_sim STATIC src/mpi_sim.cpp)
target_include_directories(mpi_sim PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(mpi_sim PUBLIC Threads::Threads)

target_compile_definitions(mpi_sim PUBLIC $<$<BOOL:${DEBUG}>:MPI_SIM_DEBUG=1>)

add_executable(mpi_sim_demo main.cpp)
target_link_libraries(mpi_sim_demo PRIVATE mpi_sim)

install(TARGETS mpi_sim_demo RUNTIME DESTINATION bin)
