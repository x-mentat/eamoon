# easunpy/crc_xmodem.py
# Implements the CRC-16/XMODEM algorithm used by Voltronic ASCII-based inverters.

def crc16_xmodem(data: bytes) -> int:
    """
    Calculates the CRC-16/XMODEM checksum.
    """
    crc = 0
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
    return crc & 0xFFFF

def adjust_crc_byte(byte: int) -> int:
    """
    Adjusts CRC bytes to avoid reserved characters (0x0A, 0x0D, 0x28).
    This is a specific requirement of the Voltronic protocol.
    """
    if byte in (0x0A, 0x0D, 0x28):
        return byte + 1
    return byte
