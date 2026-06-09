"""Compact ProductType → marketing-name map (best-effort; falls back to the
identifier). Not exhaustive — Apple ships new identifiers constantly. Lives in
``ios/`` (not ``services/``) so the model layer can stamp ``marketing`` onto
every IOSDevice.to_dict() without a circular import from services back into
ios.

Sources for cross-checking / extending:
  - https://support.apple.com/zh-cn/108044  (Apple "Identify your iPhone")
  - https://everymac.com/ultimate-mac-lookup/?identify=iPhone18,1
    (EveryMac exposes the iPhoneXX,Y identifier directly — Apple's page only
     publishes A-numbers per region.)
"""

from __future__ import annotations


MARKETING_NAMES: dict[str, str] = {
    # iPhone 17 series (2025) — provisional / verify on real devices
    "iPhone18,1": "iPhone 17 Pro",
    "iPhone18,2": "iPhone 17 Pro Max",
    "iPhone18,3": "iPhone 17",
    "iPhone18,4": "iPhone Air",
    "iPhone18,5": "iPhone 17e",

    # iPhone 16 series (2024)
    "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,5": "iPhone 16e",

    # iPhone 15 series (2023)
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",

    # iPhone 14 series (2022)
    "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",

    # iPhone SE (3rd generation, 2022)
    "iPhone14,6": "iPhone SE (3rd generation)",

    # iPhone 13 series (2021)
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",

    # iPhone 12 series (2020)
    "iPhone13,1": "iPhone 12 mini",
    "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",

    # iPhone SE (2nd generation, 2020)
    "iPhone12,8": "iPhone SE (2nd generation)",

    # iPhone 11 series (2019)
    "iPhone12,1": "iPhone 11",
    "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max",

    # iPhone XS / XS Max / XR (2018)
    "iPhone11,2": "iPhone XS",
    "iPhone11,4": "iPhone XS Max",
    "iPhone11,6": "iPhone XS Max",
    "iPhone11,8": "iPhone XR",

    # iPhone X / 8 / 8 Plus (2017)
    "iPhone10,1": "iPhone 8",
    "iPhone10,2": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X",
    "iPhone10,4": "iPhone 8",
    "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,6": "iPhone X",

    # iPhone 7 / 7 Plus (2016)
    "iPhone9,1": "iPhone 7",
    "iPhone9,2": "iPhone 7 Plus",
    "iPhone9,3": "iPhone 7",
    "iPhone9,4": "iPhone 7 Plus",

    # iPhone SE (1st generation, 2016)
    "iPhone8,4": "iPhone SE (1st generation)",

    # iPhone 6s / 6s Plus (2015)
    "iPhone8,1": "iPhone 6s",
    "iPhone8,2": "iPhone 6s Plus",

    # iPhone 6 / 6 Plus (2014)
    "iPhone7,1": "iPhone 6 Plus",
    "iPhone7,2": "iPhone 6",

    # iPhone 5s / 5c (2013), iPhone 5 (2012)
    "iPhone5,1": "iPhone 5",
    "iPhone5,2": "iPhone 5",
    "iPhone5,3": "iPhone 5c",
    "iPhone5,4": "iPhone 5c",
    "iPhone6,1": "iPhone 5s",
    "iPhone6,2": "iPhone 5s",

    # iPhone 4S (2011), iPhone 4 (2010)
    "iPhone3,1": "iPhone 4",
    "iPhone3,2": "iPhone 4",
    "iPhone3,3": "iPhone 4",
    "iPhone4,1": "iPhone 4S",

    # iPhone 3GS / 3G / original
    "iPhone1,1": "iPhone",
    "iPhone1,2": "iPhone 3G",
    "iPhone2,1": "iPhone 3GS",
}


def marketing_name(product_type: str) -> str:
    """Resolve a ProductType identifier to its marketing name, or echo the
    identifier when unknown so the caller always gets a non-empty string."""
    return MARKETING_NAMES.get(product_type, product_type)
