/** @type {import('next').NextConfig} */
const nextConfig = {
  // keep `pg` server-only (don't bundle it for the browser) — Next 14 key
  experimental: {
    serverComponentsExternalPackages: ["pg"],
  },
};
export default nextConfig;
