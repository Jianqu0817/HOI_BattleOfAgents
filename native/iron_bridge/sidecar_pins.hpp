#pragma once
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <string_view>
namespace iron::sidecar {
inline std::string hash_file(const wchar_t* path) {
    BCRYPT_ALG_HANDLE algorithm{}; BCRYPT_HASH_HANDLE hash{};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return {};
    DWORD size{}, returned{};
    BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&size), sizeof(size), &returned, 0);
    std::string object(size, '\0');
    if (BCryptCreateHash(algorithm, &hash, reinterpret_cast<PUCHAR>(object.data()), size, nullptr, 0, 0) < 0) { BCryptCloseAlgorithmProvider(algorithm, 0); return {}; }
    std::ifstream file(path, std::ios::binary); std::array<char, 65536> buffer{}; bool valid = file.is_open();
    while (file) { file.read(buffer.data(), buffer.size()); if (file.gcount() && BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), static_cast<ULONG>(file.gcount()), 0) < 0) valid = false; }
    std::array<unsigned char, 32> digest{};
    if (BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) < 0) valid = false;
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
    std::ostringstream out; for (auto b : digest) out << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(b);
    return valid ? out.str() : "";
}
inline bool pin(std::uintptr_t image, std::uintptr_t rva, std::string_view hex) {
    std::string bytes; bytes.reserve(hex.size() / 2);
    auto digit = [](char c) { return c <= '9' ? c - '0' : c - 'a' + 10; };
    for (std::size_t i = 0; i < hex.size(); i += 2) bytes += static_cast<char>((digit(hex[i]) << 4) | digit(hex[i + 1]));
    return memcmp(reinterpret_cast<void*>(image + rva), bytes.data(), bytes.size()) == 0;
}
}
