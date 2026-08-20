package chat.rules.vasl;

import VASSAL.build.AbstractConfigurable;
import VASSAL.build.Buildable;
import VASSAL.build.GameModule;
import VASSAL.build.module.PlayerRoster;
import VASSAL.build.module.documentation.HelpFile;

import javax.swing.JButton;
import javax.swing.JDialog;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JPasswordField;
import javax.swing.JScrollPane;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.SwingWorker;
import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.io.File;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.time.Duration;
import java.util.Base64;

/**
 * "Ask LLM" toolbar button: snapshots the current game with
 * GameState.saveGame(File) and asks ruleschat's POST /api/ask about it.
 *
 * Credentials (one field, auto-detected server-side):
 *   - a ruleschat account API key (minted on the /profile page), or
 *   - the user's own OpenRouter key ("sk-or-..."), in which case generation
 *     runs on their OpenRouter account (pass-through; never stored).
 *
 * Fog of war: the request carries PlayerRoster.getMySide() and the VASSAL
 * user id; the server masks the opponent's concealed/HIP units accordingly.
 */
public class AskRuleschatButton extends AbstractConfigurable {

  public static final String SERVER_URL = "url";
  public static final String API_KEY = "apikey";
  public static final String MODEL = "model";

  private String serverUrl = "http://127.0.0.1:8000";
  private String apiKey = "";
  private String model = "";

  private JButton launchButton;
  private JDialog dialog;
  private JTextField urlField;
  private JPasswordField keyField;
  private JTextField modelField;
  private JTextField questionField;
  private JTextArea output;
  private JButton askButton;

  public static String getConfigureTypeName() {
    return "Ask ruleschat button";
  }

  @Override
  public void addTo(Buildable parent) {
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

  private void showDialog() {
    if (dialog == null) {
      buildDialog();
    }
    dialog.setVisible(true);
    dialog.toFront();
    questionField.requestFocusInWindow();
  }

  private void buildDialog() {
    dialog = new JDialog(GameModule.getGameModule().getPlayerWindow(),
                         "Ask ruleschat");
    dialog.setLayout(new BorderLayout(8, 8));

    final JPanel top = new JPanel(new GridBagLayout());
    final GridBagConstraints gc = new GridBagConstraints();
    gc.insets = new Insets(2, 4, 2, 4);
    gc.fill = GridBagConstraints.HORIZONTAL;

    urlField = new JTextField(serverUrl, 24);
    keyField = new JPasswordField(apiKey, 24);
    keyField.setToolTipText("ruleschat API key (from your profile page) "
                            + "or your own OpenRouter key (sk-or-...)");
    modelField = new JTextField(model, 24);
    modelField.setToolTipText("Optional. Blank = server default. With an "
                              + "OpenRouter key use a vendor/model slug.");
    questionField = new JTextField(40);
    questionField.addActionListener(e -> ask());

    int row = 0;
    for (Object[] pair : new Object[][] {
           {"Server:", urlField},
           {"API key:", keyField},
           {"Model:", modelField},
           {"Question:", questionField}}) {
      gc.gridx = 0; gc.gridy = row; gc.weightx = 0;
      top.add(new JLabel((String) pair[0]), gc);
      gc.gridx = 1; gc.weightx = 1;
      top.add((java.awt.Component) pair[1], gc);
      row++;
    }
    gc.gridx = 1; gc.gridy = row; gc.weightx = 0;
    gc.fill = GridBagConstraints.NONE;
    gc.anchor = GridBagConstraints.EAST;
    askButton = new JButton("Ask about the current game");
    askButton.addActionListener(e -> ask());
    top.add(askButton, gc);

    dialog.add(top, BorderLayout.NORTH);

    output = new JTextArea();
    output.setEditable(false);
    output.setLineWrap(true);
    output.setWrapStyleWord(true);
    final JScrollPane scroll = new JScrollPane(output);
    scroll.setPreferredSize(new Dimension(640, 420));
    dialog.add(scroll, BorderLayout.CENTER);

    dialog.pack();
    dialog.setLocationRelativeTo(GameModule.getGameModule().getPlayerWindow());
  }

  private void append(String line) {
    output.append(line + "\n");
    output.setCaretPosition(output.getDocument().getLength());
  }

  private void ask() {
    final GameModule gm = GameModule.getGameModule();
    final String question = questionField.getText().trim();
    if (question.isEmpty()) {
      append("Type a question first.");
      return;
    }
    final String key = new String(keyField.getPassword()).trim();
    if (key.isEmpty()) {
      append("Enter an API key: generate one on your ruleschat profile page,"
             + " or use your own OpenRouter key (sk-or-...).");
      return;
    }
    final String base = urlField.getText().trim().replaceAll("/+$", "");
    final String modelChoice = modelField.getText().trim();

    // Snapshot the game if one is in progress; a rules-only question
    // without a game still works (no vsav attached).
    File snapshot = null;
    if (gm.getGameState().isGameStarted()) {
      try {
        snapshot = File.createTempFile("ask-ruleschat-", ".vsav");
        snapshot.deleteOnExit();
        gm.getGameState().saveGame(snapshot);  // EDT: touches UI state
      }
      catch (Exception ex) {
        append("Could not snapshot the game: " + ex);
        snapshot = null;
      }
    }
    final File vsav = snapshot;
    final String mySide = PlayerRoster.isActive() ? PlayerRoster.getMySide() : null;
    final String playerId = GameModule.getUserId();

    askButton.setEnabled(false);
    append("");
    append("Q: " + question);
    append(vsav != null
           ? "(board attached, side: " + (mySide != null ? mySide : "?")
             + " / " + playerId + ") thinking..."
           : "(no game loaded - rules question only) thinking...");

    new SwingWorker<String, Void>() {
      @Override
      protected String doInBackground() throws Exception {
        final StringBuilder body = new StringBuilder("{");
        body.append("\"question\":").append(Json.quote(question));
        if (vsav != null) {
          final byte[] bytes = Files.readAllBytes(vsav.toPath());
          body.append(",\"vsav\":\"data:application/octet-stream;base64,")
              .append(Base64.getEncoder().encodeToString(bytes)).append('"');
        }
        if (mySide != null && !mySide.isEmpty()) {
          body.append(",\"side\":").append(Json.quote(mySide));
        }
        if (playerId != null && !playerId.isEmpty()) {
          body.append(",\"player\":").append(Json.quote(playerId));
        }
        if (!modelChoice.isEmpty()) {
          body.append(",\"model\":").append(Json.quote(modelChoice));
        }
        body.append('}');

        // Pin HTTP/1.1: the default HTTP/2 client sends an h2c upgrade
        // handshake on plain http:// URLs, and uvicorn drops the request
        // body when it sees it (422 "body missing").
        final HttpClient client = HttpClient.newBuilder()
          .version(HttpClient.Version.HTTP_1_1)
          .connectTimeout(Duration.ofSeconds(10))
          .build();
        final HttpRequest req = HttpRequest.newBuilder(
            URI.create(base + "/api/ask"))
          .header("Content-Type", "application/json")
          .header("Authorization", "Bearer " + key)
          .timeout(Duration.ofSeconds(300))
          .POST(HttpRequest.BodyPublishers.ofString(body.toString(),
                                                    StandardCharsets.UTF_8))
          .build();

        final HttpResponse<String> resp =
          client.send(req, HttpResponse.BodyHandlers.ofString());
        final String respBody = resp.body() == null ? "" : resp.body();
        if (resp.statusCode() != 200) {
          final String detail = Json.getString(respBody, "detail");
          return "Error (HTTP " + resp.statusCode() + "): "
                 + (detail != null ? detail : respBody);
        }
        final String answer = Json.getString(respBody, "answer");
        final String remaining = Json.getRaw(respBody, "remaining_today");
        final String usedModel = Json.getString(respBody, "model");
        final StringBuilder out = new StringBuilder();
        out.append(answer != null ? answer : respBody);
        out.append("\n---\n");
        if (usedModel != null) {
          out.append("model: ").append(usedModel);
        }
        if (remaining != null) {
          out.append("  |  questions left today: ").append(remaining);
        }
        return out.toString();
      }

      @Override
      protected void done() {
        try {
          append(get());
        }
        catch (Exception ex) {
          final Throwable cause = ex.getCause() != null ? ex.getCause() : ex;
          append("Request failed: " + cause);
        }
        finally {
          askButton.setEnabled(true);
          if (vsav != null) {
            vsav.delete();
          }
        }
      }
    }.execute();
  }

  /** Just enough JSON for one flat request/response — no bundled library
   *  (the extension shares VASSAL's classloader; fewer classes, fewer
   *  collision risks). */
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

    /** [start, end) of the named top-level field's value. A real scanner —
     *  walks strings (with escapes) and nesting, so field-name-like text
     *  inside the answer string can't derail the lookup. */
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

  // --- Configurable plumbing -------------------------------------------

  @Override
  public String[] getAttributeNames() {
    return new String[] { SERVER_URL, API_KEY, MODEL };
  }

  @Override
  public String[] getAttributeDescriptions() {
    return new String[] {
      "ruleschat server URL:  ",
      "API key (ruleschat account key or OpenRouter sk-or-...):  ",
      "Model (blank = server default):  ",
    };
  }

  @Override
  public Class<?>[] getAttributeTypes() {
    return new Class<?>[] { String.class, String.class, String.class };
  }

  @Override
  public String getAttributeValueString(String key) {
    if (SERVER_URL.equals(key)) {
      return serverUrl;
    }
    if (API_KEY.equals(key)) {
      return apiKey;
    }
    if (MODEL.equals(key)) {
      return model;
    }
    return null;
  }

  @Override
  public void setAttribute(String key, Object value) {
    if (value == null) {
      return;
    }
    if (SERVER_URL.equals(key)) {
      serverUrl = value.toString().replaceAll("/+$", "");
    }
    else if (API_KEY.equals(key)) {
      apiKey = value.toString().trim();
    }
    else if (MODEL.equals(key)) {
      model = value.toString().trim();
    }
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
