package org.parlor.app;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

/** Capacitor's activity, with Parlor's WebView client: it passes the page's requests on to the chosen Parlor. */
public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        bridge.setWebViewClient(new ParlorClient(bridge));
    }
}
