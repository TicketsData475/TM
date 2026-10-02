/** @type {import('next').NextConfig} */
const nextConfig = {
  // keep `pg` server-only (don't bundle it for the browser) — Next 14 key
  experimental: {
    serverComponentsExternalPackages: ["pg"],
  },
  // On the aceify-only deployment set env ACEIFY_ONLY=1 -> bare domain "/"
  // sends visitors straight to the aceify tool. Leave it unset elsewhere
  // (local dev / a TC deployment) and "/" stays the TC page.
  async redirects() {
    if (process.env.ACEIFY_ONLY === "1") {
      return [{ source: "/", destination: "/aceify", permanent: false }];
    }
    return [];
  },
};
export default nextConfig;
