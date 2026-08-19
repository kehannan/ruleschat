package chat.rules.vasl;

import VASSAL.build.AbstractConfigurable;
import VASSAL.build.Buildable;
import VASSAL.build.GameModule;
import VASSAL.build.module.documentation.HelpFile;

import javax.swing.JButton;
import javax.swing.JDialog;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextArea;
import javax.swing.SwingWorker;
import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.FlowLayout;
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
 * Phase-0 spike: a toolbar button that snapshots the current game with
 * GameState.saveGame(File) and POSTs the bytes to ruleschat's public
 * /api/vsav/preview endpoint, proving the live-state -> .vsav -> parser
 * pipeline end to end. No LLM question yet; that is Phase 1's /api/ask.
 */
public class AskRuleschatButton extends AbstractConfigurable {

  public static final String SERVER_URL = "url";

  private String serverUrl = "http://127.0.0.1:8000";

  private JButton launchButton;
  private JDialog dialog;
  private JTextArea output;
  private JButton sendButton;

  public static String getConfigureTypeName() {
    return "Ask ruleschat button";
  }

  @Override
  public void addTo(Buildable parent) {
    launchButton = new JButton("Ask LLM");
    launchButton.setToolTipText("Send the current board to ruleschat");
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
  }

  private void buildDialog() {
    dialog = new JDialog(GameModule.getGameModule().getPlayerWindow(),
                         "Ask ruleschat");
    dialog.setLayout(new BorderLayout(8, 8));

    final JPanel top = new JPanel(new FlowLayout(FlowLayout.LEFT));
    top.add(new JLabel("Server: " + serverUrl));
    sendButton = new JButton("Send board to server");
    sendButton.addActionListener(e -> sendBoard());
    top.add(sendButton);
    dialog.add(top, BorderLayout.NORTH);

    output = new JTextArea();
    output.setEditable(false);
    output.setLineWrap(true);
    output.setWrapStyleWord(true);
    final JScrollPane scroll = new JScrollPane(output);
    scroll.setPreferredSize(new Dimension(560, 380));
    dialog.add(scroll, BorderLayout.CENTER);

    dialog.pack();
    dialog.setLocationRelativeTo(GameModule.getGameModule().getPlayerWindow());
  }

  private void append(String line) {
    output.append(line + "\n");
    output.setCaretPosition(output.getDocument().getLength());
  }

  private void sendBoard() {
    final GameModule gm = GameModule.getGameModule();
    if (!gm.getGameState().isGameStarted()) {
      append("No game in progress - open a scenario first.");
      return;
    }

    final File tmp;
    try {
      tmp = File.createTempFile("ask-ruleschat-", ".vsav");
      tmp.deleteOnExit();
      // Runs on the EDT: saveGame touches UI state (dirty flag, chatter).
      gm.getGameState().saveGame(tmp);
    }
    catch (Exception ex) {
      append("Could not snapshot the game: " + ex);
      return;
    }

    sendButton.setEnabled(false);
    append("Snapshot saved (" + tmp.length() + " bytes), sending to "
           + serverUrl + " ...");

    new SwingWorker<String, Void>() {
      @Override
      protected String doInBackground() throws Exception {
        final byte[] bytes = Files.readAllBytes(tmp.toPath());
        final String dataUrl = "data:application/octet-stream;base64,"
          + Base64.getEncoder().encodeToString(bytes);
        // Body is {"vsav": "<data URL>"}; base64 needs no JSON escaping,
        // so string assembly is safe and avoids bundling a JSON library.
        final String body = "{\"vsav\":\"" + dataUrl + "\"}";

        final HttpClient client = HttpClient.newBuilder()
          .connectTimeout(Duration.ofSeconds(10))
          .build();
        final HttpRequest req = HttpRequest.newBuilder(
            URI.create(serverUrl + "/api/vsav/preview"))
          .header("Content-Type", "application/json")
          .timeout(Duration.ofSeconds(120))
          .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
          .build();

        final HttpResponse<String> resp =
          client.send(req, HttpResponse.BodyHandlers.ofString());
        return summarize(resp, bytes.length);
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
          sendButton.setEnabled(true);
          tmp.delete();
        }
      }
    }.execute();
  }

  private static String summarize(HttpResponse<String> resp, int sentBytes) {
    final String body = resp.body() == null ? "" : resp.body();
    final StringBuilder sb = new StringBuilder();
    sb.append("HTTP ").append(resp.statusCode())
      .append("  (sent ").append(sentBytes)
      .append(" bytes, received ").append(body.length()).append(")\n");
    if (resp.statusCode() == 200) {
      sb.append("Server parsed the board. Pieces in manifest: ")
        .append(countOccurrences(body, "\"name\"")).append("\n");
    }
    final int max = 800;
    sb.append(body.length() > max ? body.substring(0, max) + " ..." : body);
    return sb.toString();
  }

  private static int countOccurrences(String haystack, String needle) {
    int count = 0;
    int idx = 0;
    while ((idx = haystack.indexOf(needle, idx)) != -1) {
      count++;
      idx += needle.length();
    }
    return count;
  }

  // --- Configurable plumbing -------------------------------------------

  @Override
  public String[] getAttributeNames() {
    return new String[] { SERVER_URL };
  }

  @Override
  public String[] getAttributeDescriptions() {
    return new String[] { "ruleschat server URL:  " };
  }

  @Override
  public Class<?>[] getAttributeTypes() {
    return new Class<?>[] { String.class };
  }

  @Override
  public String getAttributeValueString(String key) {
    if (SERVER_URL.equals(key)) {
      return serverUrl;
    }
    return null;
  }

  @Override
  public void setAttribute(String key, Object value) {
    if (SERVER_URL.equals(key) && value != null) {
      serverUrl = value.toString().replaceAll("/+$", "");
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
