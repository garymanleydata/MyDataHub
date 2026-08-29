export default {
  async fetch(request) {
    const url = new URL(request.url);
    const targetUrl = url.searchParams.get("url");

    if (!targetUrl) {
      return new Response("Missing 'url' parameter", { status: 400 });
    }

    try {
      const response = await fetch(targetUrl, {
        method: "GET",
        redirect: "follow",
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
          "Accept":
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
          "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
          "Accept-Encoding": "gzip, deflate, br",
          "Cache-Control": "max-age=0",
          "Sec-Ch-Ua":
            '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
          "Sec-Ch-Ua-Mobile": "?0",
          "Sec-Ch-Ua-Platform": '"Windows"',
          "Sec-Fetch-Dest": "document",
          "Sec-Fetch-Mode": "navigate",
          "Sec-Fetch-Site": "cross-site",
          "Sec-Fetch-User": "?1",
          "Upgrade-Insecure-Requests": "1",
        },
      });

      return new Response(response.body, {
        status: response.status,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
        },
      });
    } catch (err) {
      return new Response(`Proxy Error: ${err.message}`, { status: 500 });
    }
  },
};