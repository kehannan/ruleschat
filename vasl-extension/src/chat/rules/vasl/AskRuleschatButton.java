package chat.rules.vasl;

import VASSAL.build.AbstractConfigurable;
import VASSAL.build.Buildable;
import VASSAL.build.GameModule;
import VASSAL.build.module.PlayerRoster;
import VASSAL.build.module.documentation.HelpFile;
import VASSAL.configure.StringConfigurer;
import VASSAL.preferences.Prefs;
import VASSAL.tools.io.ObfuscatingOutputStream;

import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JDialog;
import javax.swing.JLabel;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JPasswordField;
import javax.swing.JScrollPane;
import javax.swing.JTextField;
import javax.swing.JTextPane;
import javax.swing.SwingWorker;
import javax.swing.text.SimpleAttributeSet;
import javax.swing.text.StyleConstants;
import javax.swing.text.StyledDocument;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * "Ask LLM" toolbar button: a chat dialog over ruleschat's streaming
 * POST /api/ask/stream. Each question ships an in-memory snapshot of the
 * current game (same bytes as a .vsav save, built without touching the
 * module's save state) plus recent Q/A pairs for follow-up context.
 *
 * Credentials (one field, auto-detected server-side): a ruleschat account
 * key from the /profile page, or the user's own OpenRouter "sk-or-..." key
 * (pass-through; billed to them, never stored). Settings persist in
 * VASSAL's preferences.
 */
public class AskRuleschatButton extends AbstractConfigurable {

  private static final String PREFS_CATEGORY = "Ask ruleschat";
  private static final String P_URL = "AskRuleschatServerUrl";
  private static final String P_KEY = "AskRuleschatApiKey";
  private static final String P_MODEL = "AskRuleschatModel";
  private static final String DEFAULT_URL = "https://ruleschat.com";
  private static final int MAX_HISTORY_PAIRS = 6;

  private JButton launchButton;
  private JDialog dialog;
  private JTextPane transcript;
  private JTextField questionField;
  private JButton askButton;
  private JCheckBox attachCheck;
  private JCheckBox soloCheck;
  private JLabel statusLabel;

  private final List<String[]> history = new ArrayList<>();

  private SimpleAttributeSet youStyle;
  private SimpleAttributeSet botStyle;
  private SimpleAttributeSet bodyStyle;
  private SimpleAttributeSet metaStyle;
  private SimpleAttributeSet errorStyle;

  public static String getConfigureTypeName() {
    return "Ask ruleschat button";
  }

  @Override
  public void addTo(Buildable parent) {
    final Prefs prefs = GameModule.getGameModule().getPrefs();
    prefs.addOption(PREFS_CATEGORY,
                    new StringConfigurer(P_URL, "Server URL:  ", DEFAULT_URL));
    prefs.addOption(PREFS_CATEGORY,
                    new StringConfigurer(P_KEY,
                      "API key (ruleschat or sk-or-...):  ", ""));
    prefs.addOption(PREFS_CATEGORY,
                    new StringConfigurer(P_MODEL,
                      "Model (blank = server default):  ", ""));

    launchButton = new JButton("Ask LLM");
    launchButton.setToolTipText("Ask ruleschat about the rules or the current game");
    launchButton.addActionListener(e -> showDialog());
    GameModule.getGameModule().getToolBar().add(launchButton);
    GameModule.getGameModule().getToolBar().revalidate();
  }

  @Override
  public void removeFrom(Buildable parent) {
    if (launchButton != null) {
      GameModule.getGameModule().getToolBar().remove(launchButton);
      GameModule.getGameModule().getToolBar().revalidate();
      launchButton = null;
    }
    if (dialog != null) {
      dialog.dispose();
      dialog = null;
    }
  }

  private String pref(String key, String dflt) {
    final Object v = GameModule.getGameModule().getPrefs().getValue(key);
    final String s = v == null ? "" : v.toString().trim();
    return s.isEmpty() ? dflt : s;
  }

  private void showDialog() {
    if (dialog == null) {
      buildDialog();
    }
    dialog.setVisible(true);
    dialog.toFront();
    questionField.requestFocusInWindow();
  }

  private void buildStyles() {
    youStyle = new SimpleAttributeSet();
    StyleConstants.setBold(youStyle, true);
    StyleConstants.setForeground(youStyle, new Color(0x1A, 0x56, 0x8A));

    botStyle = new SimpleAttributeSet();
    StyleConstants.setBold(botStyle, true);
    StyleConstants.setForeground(botStyle, new Color(0x2E, 0x6B, 0x2E));

    bodyStyle = new SimpleAttributeSet();

    metaStyle = new SimpleAttributeSet();
    StyleConstants.setItalic(metaStyle, true);
    StyleConstants.setForeground(metaStyle, Color.GRAY);

    errorStyle = new SimpleAttributeSet();
    StyleConstants.setForeground(errorStyle, new Color(0xB0, 0x20, 0x20));
  }

  private void buildDialog() {
    buildStyles();
    dialog = new JDialog(GameModule.getGameModule().getPlayerWindow(),
                         "Ask ruleschat");
    dialog.setLayout(new BorderLayout(6, 6));

    transcript = new JTextPane();
    transcript.setEditable(false);
    final JScrollPane scroll = new JScrollPane(transcript);
    scroll.setPreferredSize(new Dimension(680, 440));
    dialog.add(scroll, BorderLayout.CENTER);

    final JPanel south = new JPanel(new BorderLayout(4, 4));

    final JPanel inputRow = new JPanel(new BorderLayout(4, 4));
    questionField = new JTextField();
    questionField.addActionListener(e -> ask());
    inputRow.add(questionField, BorderLayout.CENTER);
    askButton = new JButton("Ask");
    askButton.addActionListener(e -> ask());
    inputRow.add(askButton, BorderLayout.EAST);
    south.add(inputRow, BorderLayout.NORTH);

    final JPanel controls = new JPanel(new FlowLayout(FlowLayout.LEFT, 8, 2));
    attachCheck = new JCheckBox("Attach board", true);
    attachCheck.setToolTipText("Send a snapshot of the current game with the question");
    controls.add(attachCheck);
    final String side = PlayerRoster.isActive() ? PlayerRoster.getMySide() : null;
    soloCheck = new JCheckBox("Solo: full view",
      side == null || side.isEmpty() || "<observer>".equals(side));
    soloCheck.setToolTipText("No hidden-unit masking. Uncheck in a two-player "
                             + "game so your opponent's concealed/HIP units stay hidden.");
    controls.add(soloCheck);
    final JButton settings = new JButton("Settings...");
    settings.addActionListener(e -> showSettings());
    controls.add(settings);
    statusLabel = new JLabel(" ");
    controls.add(statusLabel);
    south.add(controls, BorderLayout.SOUTH);

    dialog.add(south, BorderLayout.SOUTH);
    dialog.pack();
    dialog.setLocationRelativeTo(GameModule.getGameModule().getPlayerWindow());
  }

  private void showSettings() {
    final JTextField urlField = new JTextField(pref(P_URL, DEFAULT_URL), 28);
    final JPasswordField keyField =
      new JPasswordField(pref(P_KEY, ""), 28);
    final JTextField modelField = new JTextField(pref(P_MODEL, ""), 28);

    final JPanel panel = new JPanel(new GridBagLayout());
    final GridBagConstraints gc = new GridBagConstraints();
    gc.insets = new Insets(3, 4, 3, 4);
    gc.fill = GridBagConstraints.HORIZONTAL;
    int row = 0;
    for (Object[] pair : new Object[][] {
           {"Server:", urlField},
           {"API key:", keyField},
           {"Model:", modelField}}) {
      gc.gridx = 0; gc.gridy = row; gc.weightx = 0;
      panel.add(new JLabel((String) pair[0]), gc);
      gc.gridx = 1; gc.weightx = 1;
      panel.add((java.awt.Component) pair[1], gc);
      row++;
    }
    gc.gridx = 1; gc.gridy = row; gc.weightx = 1;
    panel.add(new JLabel("<html><i>Key: generate on your ruleschat profile page, "
      + "or use your own OpenRouter sk-or-... key.</i></html>"), gc);

    final int ok = JOptionPane.showConfirmDialog(
      dialog, panel, "Ask ruleschat settings",
      JOptionPane.OK_CANCEL_OPTION, JOptionPane.PLAIN_MESSAGE);
    if (ok == JOptionPane.OK_OPTION) {
      final Prefs prefs = GameModule.getGameModule().getPrefs();
      prefs.setValue(P_URL,
                     urlField.getText().trim().replaceAll("/+$", ""));
      prefs.setValue(P_KEY, new String(keyField.getPassword()).trim());
      prefs.setValue(P_MODEL, modelField.getText().trim());
      try {
        prefs.save();
      }
      catch (IOException ignored) {
        // saved on VASSAL exit anyway
      }
    }
  }

  // --- transcript helpers (EDT only) -----------------------------------

  private void appendText(String text, SimpleAttributeSet style) {
    final StyledDocument doc = transcript.getStyledDocument();
    try {
      doc.insertString(doc.getLength(), text, style);
    }
    catch (Exception ignored) {
    }
    transcript.setCaretPosition(doc.getLength());
  }

  private void setStatus(String s) {
    statusLabel.setText(s == null || s.isEmpty() ? " " : s);
  }

  // --- snapshot ---------------------------------------------------------

  /** Current game -> .vsav bytes, entirely in memory. Same format as
   *  GameState.saveGame(File) (obfuscated command stream in a zip's
   *  "savedGame" entry) but with no side effects on the module's save
   *  state, dirty flag, or last-save pointer. EDT only. */
  private static byte[] buildVsav(GameModule gm) throws IOException {
    final String save = gm.encode(gm.getGameState().getRestoreCommand());
    if (save == null) {
      throw new IOException("could not serialize the game state");
    }
    final ByteArrayOutputStream obf = new ByteArrayOutputStream();
    try (OutputStream o = new ObfuscatingOutputStream(obf)) {
      o.write(save.getBytes(StandardCharsets.UTF_8));
    }
    final ByteArrayOutputStream zip = new ByteArrayOutputStream();
    try (ZipOutputStream z = new ZipOutputStream(zip)) {
      z.putNextEntry(new ZipEntry("savedGame"));
      z.write(obf.toByteArray());
      z.closeEntry();
    }
    return zip.toByteArray();
  }

  // --- ask flow ---------------------------------------------------------

  private void ask() {
    final GameModule gm = GameModule.getGameModule();
    final String question = questionField.getText().trim();
    if (question.isEmpty()) {
      return;
    }
    final String key = pref(P_KEY, "");
    if (key.isEmpty()) {
      showSettings();
      if (pref(P_KEY, "").isEmpty()) {
        appendText("Set an API key in Settings first.\n", errorStyle);
        return;
      }
    }
    final String base = pref(P_URL, DEFAULT_URL).replaceAll("/+$", "");
    final String model = pref(P_MODEL, "");

    byte[] snapshot = null;
    if (attachCheck.isSelected() && gm.getGameState().isGameStarted()) {
      try {
        snapshot = buildVsav(gm);
      }
      catch (Exception ex) {
        appendText("Could not snapshot the game: " + ex + "\n", errorStyle);
      }
    }
    final byte[] vsav = snapshot;
    final boolean solo = soloCheck.isSelected();
    final String mySide =
      !solo && PlayerRoster.isActive() ? PlayerRoster.getMySide() : null;
    // The "RealName" preference is the player's display name. Never use
    // GameModule.getUserId() here — that is the VASSAL *password* pref
    // (used internally as the ownership id) and must not leave the app.
    final Object realName = solo ? null
      : gm.getPrefs().getValue(GameModule.REAL_NAME);
    final String playerId = realName == null ? null : realName.toString();
    final List<String[]> pastPairs = new ArrayList<>(history);

    questionField.setText("");
    questionField.setEnabled(false);
    askButton.setEnabled(false);
    appendText("You\n", youStyle);
    appendText(question + "\n\n", bodyStyle);
    appendText("ruleschat\n", botStyle);
    setStatus(vsav != null ? "sending board + question..." : "sending question...");

    new SwingWorker<Void, String[]>() {
      private final StringBuilder answer = new StringBuilder();
      private String finalKey = "answer";

      @Override
      protected Void doInBackground() {
        try {
          runRequest();
        }
        catch (Exception ex) {
          publish(new String[] {"error", "Request failed: "
            + (ex.getCause() != null ? ex.getCause() : ex)});
        }
        return null;
      }

      private void runRequest() throws Exception {
        final StringBuilder body = new StringBuilder("{");
        body.append("\"question\":").append(Json.quote(question));
        if (vsav != null) {
          body.append(",\"vsav\":\"data:application/octet-stream;base64,")
              .append(Base64.getEncoder().encodeToString(vsav)).append('"');
        }
        if (mySide != null && !mySide.isEmpty()) {
          body.append(",\"side\":").append(Json.quote(mySide));
        }
        if (playerId != null && !playerId.isEmpty()) {
          body.append(",\"player\":").append(Json.quote(playerId));
        }
        if (!model.isEmpty()) {
          body.append(",\"model\":").append(Json.quote(model));
        }
        if (!pastPairs.isEmpty()) {
          body.append(",\"history\":[");
          for (int i = 0; i < pastPairs.size(); i++) {
            if (i > 0) {
              body.append(',');
            }
            body.append('[').append(Json.quote(pastPairs.get(i)[0]))
                .append(',').append(Json.quote(pastPairs.get(i)[1]))
                .append(']');
          }
          body.append(']');
        }
        body.append('}');

        // HTTP/1.1 pinned: the default HTTP/2 client sends an h2c upgrade
        // handshake on plain http:// URLs and uvicorn drops the body.
        final HttpClient client = HttpClient.newBuilder()
          .version(HttpClient.Version.HTTP_1_1)
          .connectTimeout(Duration.ofSeconds(10))
          .build();
        final HttpRequest req = HttpRequest.newBuilder(
            URI.create(base + "/api/ask/stream"))
          .header("Content-Type", "application/json")
          .header("Authorization", "Bearer " + key)
          .timeout(Duration.ofSeconds(300))
          .POST(HttpRequest.BodyPublishers.ofString(body.toString(),
                                                    StandardCharsets.UTF_8))
          .build();

        final HttpResponse<InputStream> resp =
          client.send(req, HttpResponse.BodyHandlers.ofInputStream());
        if (resp.statusCode() != 200) {
          final String err = readAll(resp.body());
          final String detail = Json.getString(err, "detail");
          publish(new String[] {"error", "Error (HTTP " + resp.statusCode()
            + "): " + (detail != null ? detail : err)});
          return;
        }
        try (BufferedReader r = new BufferedReader(
               new InputStreamReader(resp.body(), StandardCharsets.UTF_8))) {
          String line;
          while ((line = r.readLine()) != null) {
            if (line.isEmpty()) {
              continue;
            }
            final String delta = Json.getString(line, "delta");
            if (delta != null) {
              answer.append(delta);
              publish(new String[] {"delta", delta});
              continue;
            }
            final String status = Json.getString(line, "status");
            if (status != null) {
              publish(new String[] {"status", status});
              continue;
            }
            final String error = Json.getString(line, "error");
            if (error != null) {
              publish(new String[] {"error", error});
              return;
            }
            if (Json.getRaw(line, "done") != null) {
              publish(new String[] {"meta", metaLine(line)});
            }
          }
        }
      }

      private String metaLine(String doneJson) {
        final StringBuilder sb = new StringBuilder();
        final String usedModel = Json.getString(doneJson, "model");
        final String remaining = Json.getRaw(doneJson, "remaining_today");
        final String elapsed = Json.getRaw(doneJson, "elapsed_seconds");
        if (usedModel != null) {
          sb.append(usedModel);
        }
        if (elapsed != null) {
          sb.append("  ·  ").append(elapsed).append("s");
        }
        if (remaining != null) {
          sb.append("  ·  ").append(remaining).append(" questions left today");
        }
        return sb.toString();
      }

      @Override
      protected void process(List<String[]> chunks) {
        for (String[] c : chunks) {
          switch (c[0]) {
            case "delta":
              appendText(c[1], bodyStyle);
              setStatus("answering...");
              break;
            case "status":
              setStatus(c[1]);
              break;
            case "error":
              appendText(c[1] + "\n", errorStyle);
              break;
            case "meta":
              appendText("\n" + c[1] + "\n\n", metaStyle);
              break;
            default:
              break;
          }
        }
      }

      @Override
      protected void done() {
        if (answer.length() > 0) {
          history.add(new String[] {question, answer.toString()});
          while (history.size() > MAX_HISTORY_PAIRS) {
            history.remove(0);
          }
          // process() already rendered everything; just tidy spacing when
          // the stream ended without a done line (e.g. mid-stream error).
        }
        setStatus(" ");
        questionField.setEnabled(true);
        askButton.setEnabled(true);
        questionField.requestFocusInWindow();
      }
    }.execute();
  }

  private static String readAll(InputStream in) {
    try (BufferedReader r = new BufferedReader(
           new InputStreamReader(in, StandardCharsets.UTF_8))) {
      final StringBuilder sb = new StringBuilder();
      final char[] buf = new char[4096];
      int n;
      while ((n = r.read(buf)) != -1) {
        sb.append(buf, 0, n);
      }
      return sb.toString();
    }
    catch (IOException e) {
      return "";
    }
  }

  /** Just enough JSON for flat request/response lines — no bundled library
   *  (the extension shares VASSAL's classloader; fewer classes, fewer
   *  collision risks). A real scanner: walks strings (with escapes) and
   *  nesting, so field-name-like text inside the answer can't derail it. */
  static final class Json {
    private Json() {}

    static String quote(String s) {
      final StringBuilder sb = new StringBuilder("\"");
      for (int i = 0; i < s.length(); i++) {
        final char c = s.charAt(i);
        switch (c) {
          case '"':  sb.append("\\\""); break;
          case '\\': sb.append("\\\\"); break;
          case '\n': sb.append("\\n");  break;
          case '\r': sb.append("\\r");  break;
          case '\t': sb.append("\\t");  break;
          default:
            if (c < 0x20) {
              sb.append(String.format("\\u%04x", (int) c));
            }
            else {
              sb.append(c);
            }
        }
      }
      return sb.append('"').toString();
    }

    /** Value of a top-level string field, unescaped; null if absent. */
    static String getString(String json, String field) {
      final int[] span = valueSpan(json, field);
      if (span == null || json.charAt(span[0]) != '"') {
        return null;
      }
      return unescape(json, span[0] + 1, span[1] - 1);
    }

    /** Raw text of a top-level non-string value (number/bool); null if absent. */
    static String getRaw(String json, String field) {
      final int[] span = valueSpan(json, field);
      return span == null ? null : json.substring(span[0], span[1]).trim();
    }

    private static int[] valueSpan(String json, String field) {
      int i = json.indexOf('{');
      if (i < 0) {
        return null;
      }
      i++;
      while (i < json.length()) {
        while (i < json.length()
               && (Character.isWhitespace(json.charAt(i)) || json.charAt(i) == ',')) {
          i++;
        }
        if (i >= json.length() || json.charAt(i) == '}') {
          return null;
        }
        if (json.charAt(i) != '"') {
          return null;  // malformed
        }
        final int keyStart = i + 1;
        final int keyEnd = scanString(json, i);
        final String key = unescape(json, keyStart, keyEnd - 1);
        i = keyEnd;
        while (i < json.length() && Character.isWhitespace(json.charAt(i))) {
          i++;
        }
        if (i >= json.length() || json.charAt(i) != ':') {
          return null;
        }
        i++;
        while (i < json.length() && Character.isWhitespace(json.charAt(i))) {
          i++;
        }
        final int valStart = i;
        final int valEnd = scanValue(json, i);
        if (key.equals(field)) {
          return new int[] { valStart, valEnd };
        }
        i = valEnd;
      }
      return null;
    }

    /** i at opening quote -> index just past the closing quote. */
    private static int scanString(String json, int i) {
      i++;
      while (i < json.length()) {
        final char c = json.charAt(i);
        if (c == '\\') {
          i += 2;
        }
        else if (c == '"') {
          return i + 1;
        }
        else {
          i++;
        }
      }
      return i;
    }

    /** i at first char of a value -> index just past it. */
    private static int scanValue(String json, int i) {
      final char c = json.charAt(i);
      if (c == '"') {
        return scanString(json, i);
      }
      if (c == '{' || c == '[') {
        final char close = c == '{' ? '}' : ']';
        int depth = 0;
        while (i < json.length()) {
          final char d = json.charAt(i);
          if (d == '"') {
            i = scanString(json, i);
            continue;
          }
          if (d == c) {
            depth++;
          }
          else if (d == close && --depth == 0) {
            return i + 1;
          }
          i++;
        }
        return i;
      }
      while (i < json.length() && ",}] \n\r\t".indexOf(json.charAt(i)) < 0) {
        i++;
      }
      return i;
    }

    private static String unescape(String json, int start, int end) {
      final StringBuilder sb = new StringBuilder();
      for (int i = start; i < end; i++) {
        final char c = json.charAt(i);
        if (c == '\\' && i + 1 < end) {
          final char e = json.charAt(++i);
          switch (e) {
            case 'n': sb.append('\n'); break;
            case 'r': sb.append('\r'); break;
            case 't': sb.append('\t'); break;
            case 'b': sb.append('\b'); break;
            case 'f': sb.append('\f'); break;
            case 'u':
              if (i + 4 < end) {
                sb.append((char) Integer.parseInt(json.substring(i + 1, i + 5), 16));
                i += 4;
              }
              break;
            default: sb.append(e);
          }
        }
        else {
          sb.append(c);
        }
      }
      return sb.toString();
    }
  }

  // --- Configurable plumbing (settings live in VASSAL prefs, not here) --

  @Override
  public String[] getAttributeNames() {
    return new String[0];
  }

  @Override
  public String[] getAttributeDescriptions() {
    return new String[0];
  }

  @Override
  public Class<?>[] getAttributeTypes() {
    return new Class<?>[0];
  }

  @Override
  public String getAttributeValueString(String key) {
    return null;
  }

  @Override
  public void setAttribute(String key, Object value) {
  }

  @Override
  public HelpFile getHelpFile() {
    return null;
  }

  @Override
  public Class<?>[] getAllowableConfigureComponents() {
    return new Class<?>[0];
  }
}
