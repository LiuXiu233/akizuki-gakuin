/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 立绘/头像由后端提供，允许任意来源的图片（后端地址由用户自己填）
  images: { unoptimized: true },
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
