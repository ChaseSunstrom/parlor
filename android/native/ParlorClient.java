package org.parlor.app;

import android.content.SharedPreferences;
import android.net.Uri;
import android.webkit.CookieManager;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeWebViewClient;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.CookieHandler;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONObject;

/**
 * Parlor's page is the app's own (https://localhost, a secure origin, so offline saving works even when the server is
 * plain http on a home network). This passes the page's GETs for /api, /music and /rest on to the Parlor the person
 * chose, with the cookies the app keeps for it: pictures, streams (with Range, so seeking works) and the page's own
 * requests reach the server as if the server had served the page. Requests with a body go through Capacitor's native
 * HTTP instead (a WebView doesn't show a request's body here); both share Android's cookie store.
 * /_parlor/server reads the address; ?set=ADDRESS checks it's a Parlor and keeps it; ?set= forgets it and its cookies.
 */
public class ParlorClient extends BridgeWebViewClient {
    private static final String[] PASS = {"/api/", "/music/", "/rest/"};
    private static final String[] SEND = {"range", "accept", "accept-language", "x-requested-with", "x-api-key"};
    private final SharedPreferences prefs;

    private final Bridge bridge;

    public ParlorClient(Bridge bridge) {
        super(bridge);
        this.bridge = bridge;
        prefs = bridge.getContext().getSharedPreferences("parlor", 0);
    }

    @Override
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
        Uri u = req.getUrl();
        String path = u.getPath() == null ? "" : u.getPath();
        if ("localhost".equals(u.getHost())) {
            if (path.equals("/_parlor/server")) { // only the page's own script may ask (a picture or a link can't send this header)
                if (!"1".equals(header(req, "X-Parlor-App"))) return json(403, "{\"detail\":\"Not from Parlor's page\"}", null);
                return server(u.getQueryParameter("set"));
            }
            for (String p : PASS) if (path.startsWith(p)) return pass(req, u);
        }
        return super.shouldInterceptRequest(view, req);
    }

    /** the page's renderer crashed (low memory, or a WebView bug): open the app again instead of letting Android close it */
    @Override
    public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
        super.onRenderProcessGone(view, detail);
        android.util.Log.w("Parlor", "the page's renderer went away (crashed: " + detail.didCrash() + "); starting again");
        bridge.getActivity().recreate();
        return true;
    }

    private WebResourceResponse pass(WebResourceRequest req, Uri u) {
        String server = prefs.getString("server", ""), method = req.getMethod();
        if (server.isEmpty()) return json(409, "{\"detail\":\"The app isn't connected to a Parlor\"}", null);
        if (!"GET".equals(method) && !"HEAD".equals(method)) return json(405, "{\"detail\":\"The app sends this through native HTTP\"}", null);
        try {
            String query = u.getEncodedQuery();
            HttpURLConnection c = (HttpURLConnection) new URL(server + u.getEncodedPath() + (query == null ? "" : "?" + query)).openConnection();
            c.setRequestMethod(method);
            c.setConnectTimeout(8000);
            c.setReadTimeout(60000);
            for (Map.Entry<String, String> h : req.getRequestHeaders().entrySet()) {
                String k = h.getKey().toLowerCase(Locale.ROOT);
                for (String ok : SEND) if (k.equals(ok)) c.setRequestProperty(h.getKey(), h.getValue());
            }
            if (!"parlor".equals(c.getRequestProperty("X-Requested-With"))) c.setRequestProperty("X-Requested-With", "parlor-app");
            String cookie = CookieManager.getInstance().getCookie(server);
            if (cookie != null && CookieHandler.getDefault() == null) c.setRequestProperty("Cookie", cookie); // Capacitor's cookie handler adds them otherwise
            c.setRequestProperty("Accept-Encoding", "identity"); // so Content-Length and Range mean the bytes the page gets
            int status = c.getResponseCode(); // redirects are followed: a WebView answer can't be a 3xx
            Map<String, String> headers = new HashMap<>();
            for (Map.Entry<String, List<String>> h : c.getHeaderFields().entrySet()) {
                if (h.getKey() == null) continue;
                String k = h.getKey().toLowerCase(Locale.ROOT);
                if (k.equals("set-cookie")) {
                    for (String v : h.getValue()) CookieManager.getInstance().setCookie(server, v);
                    CookieManager.getInstance().flush();
                } else if (!k.equals("transfer-encoding") && !k.equals("connection") && !k.equals("content-encoding") && !k.equals("content-type")) { // the type goes in on its own
                    headers.put(h.getKey(), String.join(", ", h.getValue()));
                }
            }
            String type = c.getContentType(), mime = type == null ? "application/octet-stream" : type.split(";")[0].trim();
            String charset = type != null && type.toLowerCase(Locale.ROOT).contains("charset=") ? type.substring(type.toLowerCase(Locale.ROOT).indexOf("charset=") + 8).trim() : null;
            if (status == 304 || (status >= 300 && status < 400)) status = 502; // never asked for, but a WebView answer can't be one
            InputStream body = status >= 400 ? c.getErrorStream() : c.getInputStream();
            if (body == null) body = new ByteArrayInputStream(new byte[0]);
            Matcher range = Pattern.compile("bytes (\\d+)-\\d+/(\\d+)").matcher(c.getHeaderField("Content-Range") == null ? "" : c.getHeaderField("Content-Range"));
            long length = c.getContentLengthLong();
            if (status == 200 && length >= 0) { // the WebView takes the length from available(), which a network stream doesn't know
                body = new FromOffset(body, 0, length);
                headers.keySet().removeIf((k) -> k.equalsIgnoreCase("Content-Length"));
            } else if (status == 206 && range.matches()) {
                // the WebView skips to the Range's start itself, in a stream it takes to begin at byte 0 (as Capacitor's own files
                // do): the server already sent from there, so the bytes before it are only counted, never read
                body = new FromOffset(body, Long.parseLong(range.group(1)), Long.parseLong(range.group(2)));
                headers.keySet().removeIf((k) -> k.equalsIgnoreCase("Content-Length"));
            }
            String reason = c.getResponseMessage();
            return new WebResourceResponse(mime, charset, status, reason == null || reason.isEmpty() ? "OK" : reason, headers, body);
        } catch (Exception e) {
            Map<String, String> h = new HashMap<>();
            h.put("X-Parlor-Offline", "1"); // the page treats it like no network
            return json(504, "{\"detail\":\"Can't reach Parlor\"}", h);
        }
    }

    private WebResourceResponse server(String set) {
        try {
            if (set == null) return json(200, new JSONObject().put("server", prefs.getString("server", "")).toString(), null);
            if (set.trim().isEmpty()) {
                prefs.edit().remove("server").apply();
                CookieManager.getInstance().removeAllCookies(null);
                return json(200, "{\"server\":\"\"}", null);
            }
            String url = set.trim().replaceAll("/+$", "");
            if (!url.matches("(?i)https?://.*")) url = "http://" + url;
            if (!url.matches("(?i)https?://[A-Za-z0-9.\\-\\[\\]:]+")) return json(400, "{\"detail\":\"That isn't an address: use something like http://192.168.1.20:8800\"}", null);
            HttpURLConnection c = (HttpURLConnection) new URL(url + "/api/auth/state").openConnection();
            c.setConnectTimeout(6000);
            c.setReadTimeout(6000);
            int status = c.getResponseCode();
            byte[] got = new byte[4096];
            int n = status == 200 ? c.getInputStream().read(got) : -1;
            if (n <= 0 || !new String(got, 0, n, StandardCharsets.UTF_8).contains("\"setup\"")) {
                return json(400, "{\"detail\":\"Something answered there, but it isn't Parlor\"}", null);
            }
            if (!url.equals(prefs.getString("server", ""))) CookieManager.getInstance().removeAllCookies(null);
            prefs.edit().putString("server", url).apply();
            return json(200, new JSONObject().put("server", url).toString(), null);
        } catch (Exception e) {
            return json(400, "{\"detail\":\"Nothing answered at that address. Is it right, and is this phone on the same network?\"}", null);
        }
    }

    /** a stream that starts part way into a file of `total` bytes: skipping up to `start` costs nothing */
    static final class FromOffset extends InputStream {
        private final InputStream in;
        private final long start, total;
        private long at;

        FromOffset(InputStream in, long start, long total) {
            this.in = in;
            this.start = start;
            this.total = total;
        }

        @Override
        public int available() {
            return (int) Math.min(Integer.MAX_VALUE, total - at);
        }

        @Override
        public long skip(long n) throws IOException {
            long free = Math.min(n, Math.max(0, start - at));
            at += free;
            long more = n > free ? in.skip(n - free) : 0;
            at += more;
            return free + more;
        }

        @Override
        public int read() throws IOException {
            if (at < start) throw new IOException("read before the range's start");
            int b = in.read();
            if (b >= 0) at++;
            return b;
        }

        @Override
        public int read(byte[] b, int off, int len) throws IOException {
            if (at < start) throw new IOException("read before the range's start");
            int n = in.read(b, off, len);
            if (n > 0) at += n;
            return n;
        }

        @Override
        public void close() throws IOException {
            in.close();
        }
    }

    private static String header(WebResourceRequest req, String name) {
        for (Map.Entry<String, String> h : req.getRequestHeaders().entrySet()) if (h.getKey().equalsIgnoreCase(name)) return h.getValue();
        return null;
    }

    private static WebResourceResponse json(int status, String body, Map<String, String> extra) {
        Map<String, String> h = extra == null ? new HashMap<>() : extra;
        h.put("Cache-Control", "no-store");
        return new WebResourceResponse("application/json", "utf-8", status, status < 300 ? "OK" : "Error", h,
                new ByteArrayInputStream(body.getBytes(StandardCharsets.UTF_8)));
    }
}
