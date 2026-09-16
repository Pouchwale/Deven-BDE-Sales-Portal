import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The portal is a pure client of the FastAPI backend — no server-side data
  // fetching, so there is nothing to configure for rewrites or images.
  // Type errors and lint errors fail the build by default in Next 16, which
  // is what we want; there is nothing to override here.

  // Dev only. Next refuses to serve its /_next/* dev assets to a request whose
  // Host is not one it was told to expect: the page returns 200 and then
  // renders nothing, because every chunk 404s. Listing the private LAN ranges
  // is what lets a phone on the same Wi-Fi actually load the portal.
  // Production builds ignore this setting entirely.
  allowedDevOrigins: ["10.*.*.*", "172.16.*.*", "192.168.*.*", "*.local"],
};

export default nextConfig;
