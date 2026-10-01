// lhci puppeteerScript (ADR 0018 §3): 측정 전에 /login 폼으로 로그인해 세션 쿠키를 만든다.
// 비밀번호는 환경변수 LHCI_PASSWORD(데모 API의 QP_ADMIN_PASSWORD와 같은 값)에서만 받는다.
// lighthouserc*.json의 disableStorageReset: true 로 쿠키가 측정 사이에 지워지지 않는다.
module.exports = async (browser, context) => {
  const page = await browser.newPage();
  try {
    const login = new URL("/login", context.url).href;
    await page.goto(login, { waitUntil: "networkidle0" });
    const cookies = await page.cookies();
    if (cookies.some((c) => c.name === "qp_session")) return;
    const password = process.env.LHCI_PASSWORD;
    if (!password) throw new Error("LHCI_PASSWORD 환경변수가 없다");
    await page.type("#password", password);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click("button[type=submit]")]);
    const after = await page.cookies();
    if (!after.some((c) => c.name === "qp_session")) throw new Error("로그인 실패 — 세션 쿠키 없음");
  } finally {
    await page.close();
  }
};
