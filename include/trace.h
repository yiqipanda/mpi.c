#ifndef TRACE_H
#define TRACE_H

#include <stdio.h>

void trace_init(const char *filename);
void trace_close();

void trace_event(
    const char *name,
    const char *category,
    const char *phase,
    int pid,
    int tid);

#endif