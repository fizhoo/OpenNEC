/*
 * NEC-2's 1980s source calls the vendor-specific SECNDS timing routine.
 * Timing is printed only for information, so a monotonic zero-valued shim is
 * sufficient for numerical regression testing on modern toolchains.
 */
void secnds_(float *seconds)
{
    *seconds = 0.0f;
}
