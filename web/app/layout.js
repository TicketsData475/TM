import "./globals.css";

export const metadata = {
  title: "Event Search",
  description: "Search events, pick a section and row",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
