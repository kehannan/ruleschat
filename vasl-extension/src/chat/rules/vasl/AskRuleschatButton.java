package chat.rules.vasl;

import VASSAL.Info;
import VASSAL.build.AbstractConfigurable;
import VASSAL.build.Buildable;
import VASSAL.build.GameModule;
import VASSAL.build.Configurable;
import VASSAL.build.module.PlayerRoster;
import VASSAL.build.module.documentation.HelpFile;
import VASSAL.counters.GamePiece;
import VASSAL.counters.PieceFinder;
import VASSAL.counters.Stack;
import VASSAL.tools.io.ObfuscatingOutputStream;
import VASL.build.module.ASLMap;
import VASL.LOS.Map.Hex;
import VASL.LOS.Map.LOSResult;
import VASL.LOS.VASLGameInterface;

import javax.swing.AbstractAction;
import javax.swing.BorderFactory;
import javax.swing.Box;
import javax.swing.BoxLayout;
import javax.swing.InputMap;
import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JComponent;
import javax.swing.JDialog;
import javax.swing.JEditorPane;
import javax.swing.JLabel;
import javax.swing.JComboBox;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JPasswordField;
import javax.swing.JScrollBar;
import javax.swing.JScrollPane;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.KeyStroke;
import javax.swing.SwingUtilities;
import javax.swing.SwingWorker;
import javax.swing.event.HyperlinkEvent;
import javax.swing.text.html.HTMLEditorKit;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Component;
import java.awt.Cursor;
import java.awt.Desktop;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.GraphicsEnvironment;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.awt.RenderingHints;
import java.awt.event.ActionEvent;
import java.awt.event.FocusAdapter;
import java.awt.event.FocusEvent;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
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
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.HashSet;
import java.util.List;
import java.util.Properties;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * "Ask LLM" toolbar button: a chat dialog over ruleschat's streaming
 * POST /api/ask/stream, styled after ruleschat.com (same palette, message
 * layout and input dock). Each question ships an in-memory snapshot of the
 * current game (same bytes as a .vsav save, built without touching the
 * module's save state) plus recent Q/A pairs for follow-up context.
 *
 * Credentials (one field, auto-detected server-side): a ruleschat account
 * key from the /profile page, or the user's own OpenRouter "sk-or-..." key
 * (pass-through; billed to them, never stored). Settings persist in
 * AskRuleschat.properties in VASSAL's prefs directory — our own file, so
 * they survive VASSAL restarts regardless of the module's Prefs lifecycle.
 */
public class AskRuleschatButton extends AbstractConfigurable {

  static final String VERSION = "0.3.8";

  // --- settings (own properties file in VASSAL's prefs dir) ---------------
  private static final String SETTINGS_FILE = "AskRuleschat.properties";
  private static final String P_URL = "server.url";
  private static final String P_KEY = "api.key";
  private static final String P_MODEL = "model";
  // 0.2.0 stored these in VASSAL's module Prefs; read once for migration.
  private static final String LEGACY_URL = "AskRuleschatServerUrl";
  private static final String LEGACY_KEY = "AskRuleschatApiKey";
  private static final String LEGACY_MODEL = "AskRuleschatModel";
  private static final String DEFAULT_URL = "https://ruleschat.com";
  private static final String DEFAULT_MODEL = "gpt-5.4";
  private static final int MAX_HISTORY_PAIRS = 6;
  private static final Pattern HEX_REFERENCE = Pattern.compile(
    "(?i)(?:\\b\\d+-)?([A-Z]+\\d+)");

  private static final ModelOption[] MODEL_OPTIONS = new ModelOption[] {
    new ModelOption(DEFAULT_MODEL, "GPT-5.4 (recommended)"),
    new ModelOption("ox-alpha", "Ox Alpha (OpenRouter preview)")
  };

  // --- palette: mirrors static/css/site-design-system.css -----------------
  static final Color PAPER = new Color(0xEEF1EF);
  static final Color PAPER2 = new Color(0xE2E7E5);
  static final Color INK = new Color(0x1E2A33);
  static final Color INK2 = new Color(0x3D4B56);
  static final Color MUTED = new Color(0x6A757D);
  static final Color HAIR = new Color(0xCFD6D5);
  static final Color ACCENT = new Color(0x2E5C7E);
  static final Color FAIL = new Color(0xA4453B);
  static final Color FIELD_BG = new Color(0xF6F8F7);   // input frame
  static final String UI_FONT =
    pickFont("Archivo", "Helvetica Neue", "Helvetica", "Arial", "SansSerif");
  static final String MONO_FONT =
    pickFont("Spline Sans Mono", "SF Mono", "Menlo", "Monaco", "Monospaced");

  private final Properties settings = new Properties();
  private File settingsFile;

  private JButton launchButton;
  private JDialog dialog;
  private JEditorPane transcript;
  private JScrollPane scroll;
  private JTextArea questionField;
  private FlatButton askButton;
  private JCheckBox attachCheck;
  private JCheckBox soloCheck;
  private JTextField losSourceField;
  private JTextField losTargetField;
  private JComboBox<Integer> losSourceLevel;
  private JComboBox<Integer> losTargetLevel;
  private JButton losPickButton;
  private boolean pickingLos;
  private ASLMap pickerMap;
  private MouseAdapter pickerListener;
  private final List<String> firingStackNames = new ArrayList<>();
  private final List<String> targetStackNames = new ArrayList<>();
  private final List<String> selectedFirerNames = new ArrayList<>();
  private final List<String> selectedTargetNames = new ArrayList<>();
  private JLabel statusLabel;
  private JLabel headerMeta;

  /** Transcript model; the HTML view is re-rendered from it. */
  final List<Message> messages = new ArrayList<>();
  private final List<String[]> history = new ArrayList<>();

  static final class Message {
    final String role;        // "user" | "assistant"
    final String time;
    String model;
    final StringBuilder text = new StringBuilder();
    String status;            // live progress line while streaming
    String error;
    String latency;           // final meta line (model · seconds · quota)
    boolean streaming;

    Message(String role, String time) {
      this.role = role;
      this.time = time;
    }
  }

  static final class ModelOption {
    final String value;
    final String label;

    ModelOption(String value, String label) {
      this.value = value;
      this.label = label;
    }

    @Override
    public String toString() {
      return label;
    }
  }

  public static String getConfigureTypeName() {
    return "Ask ruleschat (LLM) button";
  }

  @Override
  public void addTo(Buildable parent) {
    loadSettings();
    launchButton = new JButton("Ask LLM");
    launchButton.setToolTipText("Ask ruleschat about the rules or the current game");
    launchButton.addActionListener(e -> showDialog());
    GameModule.getGameModule().getToolBar().add(launchButton);
    GameModule.getGameModule().getToolBar().revalidate();
  }

  @Override
  public void removeFrom(Buildable parent) {
    stopLosPicker();
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

  // --- settings ------------------------------------------------------------

  private void loadSettings() {
    try {
      settingsFile = new File(Info.getPrefsDir(), SETTINGS_FILE);
    }
    catch (Exception e) {
      settingsFile = new File(System.getProperty("user.home"),
                              "." + SETTINGS_FILE);
    }
    if (settingsFile.isFile()) {
      try (InputStream in = new FileInputStream(settingsFile)) {
        settings.load(in);
      }
      catch (IOException e) {
        System.err.println("AskRuleschat: could not read " + settingsFile + ": " + e);
      }
    }
    // one-time migration from the 0.2.0 VASSAL-prefs keys
    if (pref(P_KEY, "").isEmpty()) {
      final String k = legacyPref(LEGACY_KEY);
      if (k != null && !k.isEmpty()) {
        settings.setProperty(P_KEY, k);
        final String u = legacyPref(LEGACY_URL);
        if (u != null && !u.isEmpty()) {
          settings.setProperty(P_URL, u);
        }
        final String m = legacyPref(LEGACY_MODEL);
        if (m != null && !m.isEmpty()) {
          settings.setProperty(P_MODEL, m);
        }
        saveSettings();
      }
    }
  }

  private static String legacyPref(String key) {
    try {
      final GameModule gm = GameModule.getGameModule();
      if (gm == null || gm.getPrefs() == null) {
        return null;
      }
      final String s = gm.getPrefs().getStoredValue(key);
      return s == null ? null : s.trim();
    }
    catch (Exception e) {
      return null;
    }
  }

  private static String normalizeModelPref(String model) {
    final String m = model == null ? "" : model.trim();
    if (m.isEmpty()) {
      return DEFAULT_MODEL;
    }
    if ("stealth/ox-alpha".equals(m)) {
      return "ox-alpha";
    }
    return m;
  }

  private static ModelOption optionForModel(String model) {
    final String wanted = normalizeModelPref(model);
    for (ModelOption opt : MODEL_OPTIONS) {
      if (opt.value.equals(wanted)) {
        return opt;
      }
    }
    return MODEL_OPTIONS[0];
  }

  private static String modelForRequest(String apiKey, String modelPref) {
    final String model = normalizeModelPref(modelPref);
    if ("ox-alpha".equals(model) && apiKey != null
        && apiKey.trim().startsWith("sk-or-")) {
      return "stealth/ox-alpha";
    }
    return model;
  }

  private void saveSettings() {
    if (settingsFile == null) {
      return;
    }
    try {
      final File dir = settingsFile.getParentFile();
      if (dir != null && !dir.isDirectory()) {
        dir.mkdirs();
      }
      try (OutputStream out = new FileOutputStream(settingsFile)) {
        settings.store(out, "Ask ruleschat (VASL extension) settings");
      }
    }
    catch (IOException e) {
      System.err.println("AskRuleschat: could not write " + settingsFile + ": " + e);
      if (dialog != null) {
        JOptionPane.showMessageDialog(dialog,
          "Settings could not be saved to\n" + settingsFile + "\n" + e,
          "Ask ruleschat", JOptionPane.WARNING_MESSAGE);
      }
    }
  }

  private String pref(String key, String dflt) {
    final String v = settings.getProperty(key);
    final String s = v == null ? "" : v.trim();
    return s.isEmpty() ? dflt : s;
  }

  static String pickFont(String... candidates) {
    try {
      final Set<String> avail = new HashSet<>(Arrays.asList(
        GraphicsEnvironment.getLocalGraphicsEnvironment()
          .getAvailableFontFamilyNames()));
      for (String c : candidates) {
        if (avail.contains(c)) {
          return c;
        }
      }
    }
    catch (Exception ignored) {
      // headless or restricted environment: fall through
    }
    return candidates[candidates.length - 1];
  }

  // --- dialog ---------------------------------------------------------------

  private void showDialog() {
    if (dialog == null) {
      buildDialog();
    }
    dialog.setVisible(true);
    dialog.toFront();
    if (pref(P_KEY, "").isEmpty()) {
      showSettings();
    }
    questionField.requestFocusInWindow();
  }

  private void buildDialog() {
    dialog = new JDialog(GameModule.getGameModule().getPlayerWindow(),
                         "Ask ruleschat");
    final String side = PlayerRoster.isActive() ? PlayerRoster.getMySide() : null;
    final boolean solo = side == null || side.isEmpty() || "<observer>".equals(side);
    dialog.setContentPane(buildContent(solo));
    dialog.pack();
    dialog.setMinimumSize(new Dimension(560, 420));
    dialog.setLocationRelativeTo(GameModule.getGameModule().getPlayerWindow());
  }

  /** The whole UI as one panel (no window), so it can also be rendered
   *  off-screen for previews. */
  JPanel buildContent(boolean soloDefault) {
    final JPanel root = new JPanel(new BorderLayout());
    root.setBackground(PAPER);
    root.setPreferredSize(new Dimension(780, 640));

    // -- header bar (site topbar: ink background, wordmark, settings) --
    final JPanel header = new JPanel(new BorderLayout());
    header.setBackground(INK);
    header.setBorder(BorderFactory.createEmptyBorder(10, 16, 10, 12));
    final JLabel brand = new JLabel("ASL Ruleschat");
    brand.setFont(new Font(UI_FONT, Font.BOLD, 15));
    brand.setForeground(Color.WHITE);
    final JLabel sub = new JLabel("  ·  ask about the rules or the current game");
    sub.setFont(new Font(MONO_FONT, Font.PLAIN, 11));
    sub.setForeground(new Color(0xA9B4BC));
    final JPanel left = new JPanel(new FlowLayout(FlowLayout.LEFT, 0, 0));
    left.setOpaque(false);
    left.add(brand);
    left.add(sub);
    header.add(left, BorderLayout.WEST);
    final JPanel right = new JPanel(new FlowLayout(FlowLayout.RIGHT, 10, 0));
    right.setOpaque(false);
    headerMeta = new JLabel();
    headerMeta.setFont(new Font(MONO_FONT, Font.PLAIN, 11));
    headerMeta.setForeground(new Color(0xA9B4BC));
    right.add(headerMeta);
    final FlatButton settingsBtn = new FlatButton("Settings", null, Color.WHITE,
                                                  new Color(0x5A6770));
    settingsBtn.addActionListener(e -> showSettings());
    right.add(settingsBtn);
    header.add(right, BorderLayout.EAST);
    root.add(header, BorderLayout.NORTH);

    // -- transcript --
    transcript = new JEditorPane();
    transcript.setEditorKit(new HTMLEditorKit());
    transcript.setEditable(false);
    transcript.setBackground(PAPER);
    transcript.setBorder(BorderFactory.createEmptyBorder());
    transcript.addHyperlinkListener(e -> {
      if (e.getEventType() == HyperlinkEvent.EventType.ACTIVATED
          && e.getURL() != null) {
        try {
          Desktop.getDesktop().browse(e.getURL().toURI());
        }
        catch (Exception ignored) {
          // no browser available
        }
      }
    });
    scroll = new JScrollPane(transcript);
    scroll.setBorder(BorderFactory.createEmptyBorder());
    scroll.getViewport().setBackground(PAPER);
    scroll.getVerticalScrollBar().setUnitIncrement(16);
    root.add(scroll, BorderLayout.CENTER);

    // -- input dock --
    final JPanel dock = new JPanel(new BorderLayout(0, 8));
    dock.setBackground(PAPER);
    dock.setBorder(BorderFactory.createCompoundBorder(
      BorderFactory.createMatteBorder(1, 0, 0, 0, HAIR),
      BorderFactory.createEmptyBorder(12, 16, 12, 16)));

    final JPanel frame = new JPanel(new BorderLayout(10, 0));
    frame.setBackground(FIELD_BG);
    frame.setBorder(frameBorder(INK2));
    questionField = new JTextArea(2, 40);
    questionField.setLineWrap(true);
    questionField.setWrapStyleWord(true);
    questionField.setFont(new Font(UI_FONT, Font.PLAIN, 13));
    questionField.setForeground(INK);
    questionField.setBackground(FIELD_BG);
    questionField.setCaretColor(INK);
    questionField.setBorder(BorderFactory.createEmptyBorder(2, 2, 2, 2));
    final InputMap im = questionField.getInputMap(JComponent.WHEN_FOCUSED);
    im.put(KeyStroke.getKeyStroke("ENTER"), "askruleschat-send");
    im.put(KeyStroke.getKeyStroke("shift ENTER"), "insert-break");
    questionField.getActionMap().put("askruleschat-send", new AbstractAction() {
      @Override
      public void actionPerformed(ActionEvent e) {
        ask();
      }
    });
    questionField.addFocusListener(new FocusAdapter() {
      @Override
      public void focusGained(FocusEvent e) {
        frame.setBorder(frameBorder(ACCENT));
      }

      @Override
      public void focusLost(FocusEvent e) {
        frame.setBorder(frameBorder(INK2));
      }
    });
    frame.add(questionField, BorderLayout.CENTER);
    askButton = new FlatButton("Send", ACCENT, Color.WHITE, null);
    askButton.addActionListener(e -> ask());
    final JPanel btnWrap = new JPanel(new BorderLayout());
    btnWrap.setOpaque(false);
    btnWrap.add(askButton, BorderLayout.SOUTH);
    frame.add(btnWrap, BorderLayout.EAST);
    dock.add(frame, BorderLayout.CENTER);

    final JPanel controls = new JPanel(new BorderLayout());
    controls.setOpaque(false);
    final JPanel toggles = new JPanel(new FlowLayout(FlowLayout.LEFT, 14, 0));
    toggles.setOpaque(false);
    attachCheck = styledCheck("Attach board", true,
      "Send a snapshot of the current game with the question");
    soloCheck = styledCheck("Solo: full view", soloDefault,
      "No hidden-unit masking. Uncheck in a two-player game so your "
      + "opponent's concealed/HIP units stay hidden.");
    toggles.add(attachCheck);
    toggles.add(soloCheck);
    final JLabel hint = new JLabel("enter to send  ·  shift-enter for newline");
    hint.setFont(new Font(MONO_FONT, Font.PLAIN, 11));
    hint.setForeground(MUTED);
    toggles.add(hint);
    controls.add(toggles, BorderLayout.WEST);
    final JPanel losControls = new JPanel(new FlowLayout(FlowLayout.LEFT, 4, 0));
    losControls.setOpaque(false);
    losControls.add(compactLabel("LOS"));
    losSourceField = compactField("Source hex");
    losControls.add(losSourceField);
    losSourceLevel = levelBox("Source level");
    losControls.add(losSourceLevel);
    losControls.add(compactLabel("to"));
    losTargetField = compactField("Target hex");
    losControls.add(losTargetField);
    losTargetLevel = levelBox("Target level");
    losControls.add(losTargetLevel);
    losPickButton = new JButton("Pick");
    losPickButton.setToolTipText("Click firing Location, then target Location on the map");
    losPickButton.addActionListener(e -> toggleLosPicker());
    losControls.add(losPickButton);
    final JButton firerCountersButton = new JButton("Firer");
    firerCountersButton.setToolTipText("Choose which counters in the firing stack attack");
    firerCountersButton.addActionListener(e -> editStackSelection(
      "Firing counters", firingStackNames, selectedFirerNames));
    losControls.add(firerCountersButton);
    final JButton targetCountersButton = new JButton("Target");
    targetCountersButton.setToolTipText("Choose which counters in the target stack are attacked");
    targetCountersButton.addActionListener(e -> editStackSelection(
      "Target counters", targetStackNames, selectedTargetNames));
    losControls.add(targetCountersButton);
    final JButton swapLosButton = new JButton("Swap");
    swapLosButton.setToolTipText("Swap firing and target Locations");
    swapLosButton.addActionListener(e -> swapLosLocations());
    losControls.add(swapLosButton);
    final JButton clearLosButton = new JButton("Clear");
    clearLosButton.setToolTipText("Clear selected LOS Locations");
    clearLosButton.addActionListener(e -> clearLosLocations());
    losControls.add(clearLosButton);
    controls.add(losControls, BorderLayout.CENTER);
    statusLabel = new JLabel();
    statusLabel.setFont(new Font(MONO_FONT, Font.PLAIN, 11));
    statusLabel.setForeground(MUTED);
    controls.add(statusLabel, BorderLayout.EAST);
    dock.add(controls, BorderLayout.SOUTH);
    root.add(dock, BorderLayout.SOUTH);

    refreshHeaderMeta();
    setStatus(null);
    renderTranscript();
    return root;
  }

  private static javax.swing.border.Border frameBorder(Color c) {
    return BorderFactory.createCompoundBorder(
      BorderFactory.createLineBorder(c, 1),
      BorderFactory.createEmptyBorder(8, 10, 8, 8));
  }

  private static JCheckBox styledCheck(String text, boolean on, String tip) {
    final JCheckBox cb = new JCheckBox(text, on);
    cb.setOpaque(false);
    cb.setFont(new Font(UI_FONT, Font.PLAIN, 12));
    cb.setForeground(INK2);
    cb.setToolTipText(tip);
    return cb;
  }

  private static JLabel compactLabel(String text) {
    final JLabel label = new JLabel(text);
    label.setFont(new Font(MONO_FONT, Font.PLAIN, 11));
    label.setForeground(MUTED);
    return label;
  }

  private static JTextField compactField(String tip) {
    final JTextField field = new JTextField(5);
    field.setFont(new Font(MONO_FONT, Font.PLAIN, 11));
    field.setToolTipText(tip);
    return field;
  }

  private static JComboBox<Integer> levelBox(String tip) {
    final JComboBox<Integer> box = new JComboBox<>(new Integer[] {0, 1, 2, 3, 4});
    box.setFont(new Font(MONO_FONT, Font.PLAIN, 11));
    box.setToolTipText(tip);
    return box;
  }

  private void toggleLosPicker() {
    if (pickingLos) {
      stopLosPicker();
      return;
    }
    pickerMap = findAslMap(GameModule.getGameModule());
    if (pickerMap == null) {
      setStatus("map unavailable for LOS pick");
      return;
    }
    pickerListener = new MouseAdapter() {
      @Override
      public void mouseReleased(MouseEvent event) {
        if (event.getButton() != MouseEvent.BUTTON1) {
          return;
        }
        final String location = pickerMap.locationName(event.getPoint());
        final Matcher m = HEX_REFERENCE.matcher(location == null ? "" : location);
        String hex = null;
        while (m.find()) {
          hex = m.group(1).toUpperCase();
        }
        if (hex == null) {
          setStatus("click a board hex");
          return;
        }
        if (losSourceField.getText().trim().isEmpty()) {
          losSourceField.setText(hex);
          selectedTargetNames.clear();
          targetStackNames.clear();
          selectStack(event, firingStackNames);
          selectAll(firingStackNames, selectedFirerNames);
          setStatus(selectedFirerNames.isEmpty()
            ? "no stack found; select target Location"
            : selectedFirerNames.size() + " counter(s) selected; select target Location");
        }
        else {
          losTargetField.setText(hex);
          selectStack(event, targetStackNames);
          selectAll(targetStackNames, selectedTargetNames);
          stopLosPicker();
          setStatus(selectedTargetNames.isEmpty()
            ? "LOS Locations selected"
            : "LOS Locations + " + selectedTargetNames.size() + " target counter(s) selected");
        }
      }
    };
    pickerMap.addLocalMouseListener(pickerListener);
    pickingLos = true;
    losPickButton.setText("Cancel");
    setStatus("select firing Location");
  }

  private void stopLosPicker() {
    if (pickerMap != null && pickerListener != null) {
      pickerMap.removeLocalMouseListener(pickerListener);
    }
    pickerMap = null;
    pickerListener = null;
    pickingLos = false;
    if (losPickButton != null) {
      losPickButton.setText("Pick");
    }
  }

  /** Capture a clicked stack so attacks use its actual counters, rather
   * than every unit in the same hex. */
  private void selectStack(MouseEvent event, List<String> selectedNames) {
    selectedNames.clear();
    try {
      final GamePiece picked = pickerMap.findPiece(event.getPoint(), PieceFinder.PIECE_IN_STACK);
      if (picked == null) {
        return;
      }
      Stack stack = null;
      if (picked instanceof Stack) {
        stack = (Stack) picked;
      }
      else if (picked.getParent() instanceof Stack) {
        stack = (Stack) picked.getParent();
      }
      if (stack != null) {
        for (GamePiece piece : stack.asList()) {
          addSelectedPiece(piece, selectedNames);
        }
      }
      else {
        addSelectedPiece(picked, selectedNames);
      }
    }
    catch (Exception e) {
      System.err.println("AskRuleschat: could not read selected stack: " + e);
    }
  }

  private void addSelectedPiece(GamePiece piece, List<String> selectedNames) {
    if (piece != null && piece.getName() != null && !piece.getName().trim().isEmpty()) {
      selectedNames.add(piece.getName().trim());
    }
  }

  private static void selectAll(List<String> source, List<String> selected) {
    selected.clear();
    selected.addAll(source);
  }

  /** Allow a player to fire only part of a picked stack (for example, a
   * squad and its LMG but not another squad sharing the Location). */
  private void editStackSelection(String title, List<String> stackNames,
                                  List<String> selectedNames) {
    if (stackNames.isEmpty()) {
      setStatus("pick the " + title.toLowerCase() + " on the map first");
      return;
    }
    final JPanel choices = new JPanel();
    choices.setLayout(new BoxLayout(choices, BoxLayout.Y_AXIS));
    final List<JCheckBox> boxes = new ArrayList<>();
    for (String name : stackNames) {
      final JCheckBox box = new JCheckBox(name, selectedNames.contains(name));
      box.setFont(new Font(UI_FONT, Font.PLAIN, 12));
      box.setOpaque(false);
      boxes.add(box);
      choices.add(box);
    }
    final JScrollPane pane = new JScrollPane(choices);
    pane.setPreferredSize(new Dimension(320, Math.min(300, 36 + stackNames.size() * 26)));
    if (JOptionPane.showConfirmDialog(dialog, pane, title,
        JOptionPane.OK_CANCEL_OPTION, JOptionPane.PLAIN_MESSAGE) != JOptionPane.OK_OPTION) {
      return;
    }
    selectedNames.clear();
    for (int i = 0; i < boxes.size(); i++) {
      if (boxes.get(i).isSelected()) {
        selectedNames.add(stackNames.get(i));
      }
    }
    setStatus(selectedNames.size() + " " + title.toLowerCase() + " selected");
  }

  private void swapLosLocations() {
    selectedFirerNames.clear();
    selectedTargetNames.clear();
    firingStackNames.clear();
    targetStackNames.clear();
    final String source = losSourceField.getText();
    losSourceField.setText(losTargetField.getText());
    losTargetField.setText(source);
    final Object level = losSourceLevel.getSelectedItem();
    losSourceLevel.setSelectedItem(losTargetLevel.getSelectedItem());
    losTargetLevel.setSelectedItem(level);
  }

  private void clearLosLocations() {
    stopLosPicker();
    selectedFirerNames.clear();
    selectedTargetNames.clear();
    firingStackNames.clear();
    targetStackNames.clear();
    losSourceField.setText("");
    losTargetField.setText("");
    losSourceLevel.setSelectedIndex(0);
    losTargetLevel.setSelectedIndex(0);
  }

  private void refreshHeaderMeta() {
    if (headerMeta == null) {
      return;
    }
    String host = pref(P_URL, DEFAULT_URL);
    try {
      final String h = URI.create(host).getHost();
      if (h != null) {
        host = h;
      }
    }
    catch (Exception ignored) {
      // keep raw
    }
    final String model = normalizeModelPref(pref(P_MODEL, DEFAULT_MODEL));
    final boolean hasKey = !pref(P_KEY, "").isEmpty();
    headerMeta.setText(host + (model.isEmpty() ? "" : "  ·  " + model)
                       + (hasKey ? "" : "  ·  no API key"));
  }

  private void showSettings() {
    final JTextField urlField = new JTextField(pref(P_URL, DEFAULT_URL), 30);
    final JPasswordField keyField = new JPasswordField(pref(P_KEY, ""), 30);
    final JComboBox<ModelOption> modelBox = new JComboBox<>(MODEL_OPTIONS);
    modelBox.setSelectedItem(optionForModel(pref(P_MODEL, DEFAULT_MODEL)));
    modelBox.setFont(new Font(UI_FONT, Font.PLAIN, 12));
    for (JTextField f : new JTextField[] {urlField, keyField}) {
      f.setFont(new Font(MONO_FONT, Font.PLAIN, 12));
    }

    final JPanel panel = new JPanel(new GridBagLayout());
    final GridBagConstraints gc = new GridBagConstraints();
    gc.insets = new Insets(4, 4, 4, 4);
    gc.fill = GridBagConstraints.HORIZONTAL;
    int row = 0;
    for (Object[] pair : new Object[][] {
           {"Server", urlField},
           {"API key", keyField},
           {"Model", modelBox}}) {
      gc.gridx = 0; gc.gridy = row; gc.weightx = 0;
      final JLabel l = new JLabel((String) pair[0]);
      l.setFont(new Font(UI_FONT, Font.PLAIN, 12));
      panel.add(l, gc);
      gc.gridx = 1; gc.weightx = 1;
      panel.add((Component) pair[1], gc);
      row++;
    }
    gc.gridx = 0; gc.gridy = row; gc.gridwidth = 2; gc.weightx = 1;
    final JLabel help = new JLabel("<html><div style='width:340px'>"
      + "<b>API key</b>: generate one on your ruleschat profile page "
      + "(ruleschat.com/profile), or use your own OpenRouter sk-or-... key "
      + "(Ox Alpha is sent as its OpenRouter slug for pass-through keys)."
      + "<br><br><span style='color:#6A757D'>Saved to " + settingsFile
      + " — you only enter this once.</span></div></html>");
    help.setFont(new Font(UI_FONT, Font.PLAIN, 11));
    panel.add(help, gc);

    final int ok = JOptionPane.showConfirmDialog(
      dialog, panel, "Ask ruleschat settings",
      JOptionPane.OK_CANCEL_OPTION, JOptionPane.PLAIN_MESSAGE);
    if (ok == JOptionPane.OK_OPTION) {
      settings.setProperty(P_URL,
                           urlField.getText().trim().replaceAll("/+$", ""));
      settings.setProperty(P_KEY, new String(keyField.getPassword()).trim());
      final ModelOption selected = (ModelOption) modelBox.getSelectedItem();
      settings.setProperty(P_MODEL,
                           selected == null ? DEFAULT_MODEL : selected.value);
      saveSettings();
      refreshHeaderMeta();
    }
  }

  // --- transcript rendering (EDT only) ------------------------------------

  private void setStatus(String s) {
    if (statusLabel == null) {
      return;
    }
    final boolean busy = s != null && !s.isEmpty();
    statusLabel.setForeground(busy ? ACCENT : MUTED);
    statusLabel.setText("●  " + (busy ? s : "ready"));
  }

  private static String now() {
    return LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm"));
  }

  private static String css() {
    return "body { background-color: #EEF1EF; color: #1E2A33; font-family: "
      + UI_FONT + "; font-size: 13px; margin: 14px 20px 8px 20px; }\n"
      + "p { margin: 0 0 9px 0; }\n"
      + "h3 { font-size: 13px; margin: 12px 0 6px 0; color: #1E2A33; }\n"
      + "ul, ol { margin: 0 0 9px 20px; }\n"
      + "li { margin: 0 0 4px 0; }\n"
      + "a { color: #2E5C7E; }\n"
      + "code { font-family: " + MONO_FONT + "; font-size: 11px; "
      + "background-color: #E2E7E5; }\n"
      + "pre { font-family: " + MONO_FONT + "; font-size: 11px; "
      + "background-color: #E2E7E5; margin: 0 0 9px 0; }\n"
      + "td.meta { font-family: " + MONO_FONT + "; font-size: 10px; "
      + "color: #6A757D; padding: 0 0 5px 0; }\n"
      + "td.bubble { background-color: #E2E7E5; padding: 10px 14px; "
      + "font-size: 13px; color: #1E2A33; }\n"
      + "td.bubbleedge { background-color: #CFD6D5; }\n"
      + "td.content { padding: 0; }\n"
      + "p.status { font-family: " + MONO_FONT + "; font-size: 11px; "
      + "color: #6A757D; margin: 4px 0 9px 0; }\n"
      + "p.err { color: #A4453B; margin: 4px 0 9px 0; }\n"
      + "td.lat { font-family: " + MONO_FONT + "; font-size: 10px; "
      + "color: #6A757D; padding: 7px 0 0 0; }\n"
      + "div.rule { border-top: 1px solid #CFD6D5; margin: 8px 0 12px 0; "
      + "font-size: 1px; }\n"
      + "div.latrule { border-top: 1px solid #CFD6D5; margin: 10px 0 0 0; "
      + "font-size: 1px; }\n"
      + "td.gap { padding: 10px 0 0 0; }\n"
      + "p.empty { font-size: 22px; color: #1E2A33; margin: 40px 0 0 0; }\n"
      + "p.emptysub { color: #6A757D; margin: 6px 0 0 0; }\n"
      + "th { text-align: left; font-size: 11px; padding: 3px 8px; "
      + "background-color: #E2E7E5; }\n"
      + "table.md td { font-size: 11px; padding: 3px 8px; }\n";
  }

  /** Rebuild the transcript HTML from the message model. */
  void renderTranscript() {
    if (transcript == null) {
      return;
    }
    final StringBuilder h = new StringBuilder();
    h.append("<html><head><style>").append(css()).append("</style></head><body>");
    if (messages.isEmpty()) {
      h.append("<p class='empty'><b>Ask a rules question.</b></p>")
       .append("<p class='emptysub'>With a game loaded and \"Attach board\" "
               + "checked, the answer uses the exact units, positions and "
               + "terrain on your map.</p>");
    }
    for (Message m : messages) {
      if ("user".equals(m.role)) {
        h.append("<table width='100%' cellspacing='0' cellpadding='0'>")
         .append("<tr><td class='meta' align='right' colspan='2'>YOU &middot; ")
         .append(m.time).append("</td></tr>")
         .append("<tr><td width='22%'></td>")
         .append("<td class='bubbleedge'><table width='100%' cellspacing='1' "
                 + "cellpadding='0'><tr><td class='bubble'>")
         .append(Md.escape(m.text.toString()).replace("\n", "<br>"))
         .append("</td></tr></table></td></tr>")
         .append("<tr><td class='gap' colspan='2'></td></tr></table>");
      }
      else {
        h.append("<table width='100%' cellspacing='0' cellpadding='0'>")
         .append("<tr><td class='meta'><font color='#2E5C7E'>ASSISTANT</font>"
                 + " &middot; ").append(m.time);
        if (m.model != null && !m.model.isEmpty()) {
          h.append(" &middot; ").append(Md.escape(m.model));
        }
        h.append("</td></tr><tr><td class='content'>");
        if (m.text.length() > 0) {
          h.append(Md.toHtml(m.text.toString()));
        }
        if (m.streaming && m.status != null) {
          h.append("<p class='status'>").append(Md.escape(m.status)).append("</p>");
        }
        if (m.error != null) {
          h.append("<p class='err'>").append(Md.escape(m.error)).append("</p>");
        }
        h.append("</td></tr>");
        if (m.latency != null) {
          h.append("<tr><td><div class='latrule'></div>"
                   + "<table width='100%' cellspacing='0' cellpadding='0'>"
                   + "<tr><td class='lat'>").append(Md.escape(m.latency))
           .append("</td></tr></table></td></tr>");
        }
        h.append("<tr><td class='gap'></td></tr></table>");
      }
    }
    h.append("</body></html>");

    final JScrollBar bar = scroll == null ? null : scroll.getVerticalScrollBar();
    final boolean atBottom = bar == null
      || bar.getValue() + bar.getVisibleAmount() >= bar.getMaximum() - 40;
    final int keep = bar == null ? 0 : bar.getValue();
    transcript.setText(h.toString());
    if (bar != null) {
      SwingUtilities.invokeLater(() ->
        bar.setValue(atBottom ? bar.getMaximum() : keep));
    }
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

  private static ASLMap findAslMap(Configurable node) {
    if (node instanceof ASLMap) {
      return (ASLMap) node;
    }
    for (Configurable child : node.getConfigureComponents()) {
      ASLMap found = findAslMap(child);
      if (found != null) {
        return found;
      }
    }
    return null;
  }

  /** Ask VASL itself for LOS, using explicit Locations when selected. */
  private static String nativeLosForQuestion(String question, GameModule gm,
                                             String sourceOverride,
                                             String targetOverride,
                                             int sourceLevel, int targetLevel) {
    String from = sourceOverride == null ? null : sourceOverride.trim().toUpperCase();
    String to = targetOverride == null ? null : targetOverride.trim().toUpperCase();
    if (from != null && from.isEmpty()) {
      from = null;
    }
    if (to != null && to.isEmpty()) {
      to = null;
    }
    Matcher matcher = HEX_REFERENCE.matcher(question);
    while ((from == null || to == null) && matcher.find()) {
      if (from == null) {
        from = matcher.group(1).toUpperCase();
      }
      else {
        to = matcher.group(1).toUpperCase();
        break;
      }
    }
    if (from == null || to == null || from.equals(to)) {
      return null;
    }
    try {
      ASLMap gameMap = findAslMap(gm);
      if (gameMap == null || gameMap.getVASLMap() == null) {
        return null;
      }
      VASL.LOS.Map.Map losMap = gameMap.getVASLMap();
      Hex source = losMap.getHex(from);
      Hex target = losMap.getHex(to);
      if (source == null || target == null) {
        return null;
      }
      VASLGameInterface game = new VASLGameInterface(gameMap, losMap);
      game.updatePieces();
      LOSResult result = new LOSResult();
      VASL.LOS.Map.Location sourceLocation = source.getCenterLocation();
      VASL.LOS.Map.Location targetLocation = target.getCenterLocation();
      for (int i = 0; i < sourceLevel && sourceLocation.getUpLocation() != null; i++) {
        sourceLocation = sourceLocation.getUpLocation();
      }
      for (int i = 0; i < targetLevel && targetLocation.getUpLocation() != null; i++) {
        targetLocation = targetLocation.getUpLocation();
      }
      losMap.LOS(sourceLocation, false, targetLocation,
                 false, result, game);
      return "{\"source\":" + Json.quote(from)
        + ",\"target\":" + Json.quote(to)
        + ",\"blocked\":" + result.isBlocked()
        + ",\"reason\":" + Json.quote(result.getReason() == null ? "" : result.getReason())
        + ",\"hindrance\":" + result.getHindrance()
        + ",\"range\":" + result.getRange()
        + ",\"source_level\":" + sourceLocation.getLevelInHex()
        + ",\"target_level\":" + targetLocation.getLevelInHex() + "}";
    }
    catch (Exception e) {
      System.err.println("AskRuleschat: native LOS unavailable: " + e);
      return null;
    }
  }

  private static String currentPhase(GameModule gm) {
    Object phase = gm.getProperty("PhaseName");
    if (phase == null) {
      ASLMap map = findAslMap(gm);
      phase = map == null ? null : map.getProperty("PhaseName");
    }
    return phase == null ? null : phase.toString().trim();
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
        final Message m = new Message("assistant", now());
        m.error = "Set an API key in Settings first (top right).";
        messages.add(m);
        renderTranscript();
        return;
      }
    }
    final String base = pref(P_URL, DEFAULT_URL).replaceAll("/+$", "");
    final String model = modelForRequest(key, pref(P_MODEL, DEFAULT_MODEL));
    final String nativeLos = nativeLosForQuestion(question, gm,
      losSourceField == null ? null : losSourceField.getText(),
      losTargetField == null ? null : losTargetField.getText(),
      losSourceLevel == null ? 0 : (Integer) losSourceLevel.getSelectedItem(),
      losTargetLevel == null ? 0 : (Integer) losTargetLevel.getSelectedItem());
    final String gamePhase = currentPhase(gm);
    final List<String> pickedFirers = new ArrayList<>(selectedFirerNames);
    final List<String> pickedTargets = new ArrayList<>(selectedTargetNames);

    byte[] snapshot = null;
    String snapshotError = null;
    if (attachCheck.isSelected() && gm.getGameState().isGameStarted()) {
      try {
        snapshot = buildVsav(gm);
      }
      catch (Exception ex) {
        snapshotError = "Could not snapshot the game: " + ex;
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

    final Message userMsg = new Message("user", now());
    userMsg.text.append(question);
    messages.add(userMsg);
    final Message bot = new Message("assistant", now());
    bot.model = model.isEmpty() ? null : model;
    bot.streaming = true;
    bot.status = vsav != null ? "sending board + question…" : "sending question…";
    if (snapshotError != null) {
      bot.error = snapshotError;
    }
    messages.add(bot);
    renderTranscript();
    setStatus(bot.status);

    new SwingWorker<Void, String[]>() {
      private final StringBuilder answer = new StringBuilder();

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
        if (nativeLos != null) {
          body.append(",\"native_los\":").append(nativeLos);
        }
        if (gamePhase != null && !gamePhase.isEmpty()) {
          body.append(",\"game_phase\":").append(Json.quote(gamePhase));
        }
        if (!pickedFirers.isEmpty()) {
          body.append(",\"selected_firers\":[");
          for (int i = 0; i < pickedFirers.size(); i++) {
            if (i > 0) {
              body.append(',');
            }
            body.append(Json.quote(pickedFirers.get(i)));
          }
          body.append(']');
        }
        if (!pickedTargets.isEmpty()) {
          body.append(",\"selected_targets\":[");
          for (int i = 0; i < pickedTargets.size(); i++) {
            if (i > 0) {
              body.append(',');
            }
            body.append(Json.quote(pickedTargets.get(i)));
          }
          body.append(']');
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
          .timeout(Duration.ofSeconds(600))
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

      /** "gpt-5.4  ·  14.2s  ·  49,835 in / 1,057 out  ·  $0.14" */
      private String metaLine(String doneJson) {
        final StringBuilder sb = new StringBuilder();
        final String usedModel = Json.getString(doneJson, "model");
        final String elapsed = Json.getRaw(doneJson, "elapsed_seconds");
        final String tin = Json.getRaw(doneJson, "tokens_in");
        final String tout = Json.getRaw(doneJson, "tokens_out");
        final String cost = Json.getRaw(doneJson, "cost_usd");
        if (usedModel != null) {
          sb.append(usedModel);
        }
        if (elapsed != null) {
          sb.append("  ·  ").append(elapsed).append("s");
        }
        if (tin != null && !"null".equals(tin) && tout != null && !"null".equals(tout)) {
          sb.append("  ·  ").append(thousands(tin)).append(" in / ")
            .append(thousands(tout)).append(" out");
        }
        if (cost != null && !"null".equals(cost)) {
          try {
            final double c = Double.parseDouble(cost);
            sb.append("  ·  ").append(c < 0.01 && c > 0
              ? String.format(java.util.Locale.US, "$%.3f", c)
              : String.format(java.util.Locale.US, "$%.2f", c));
          }
          catch (NumberFormatException ignored) {
            // leave cost off
          }
        }
        return sb.toString();
      }

      private String thousands(String intText) {
        try {
          return String.format(java.util.Locale.US, "%,d",
                               Long.parseLong(intText.trim()));
        }
        catch (NumberFormatException e) {
          return intText;
        }
      }

      @Override
      protected void process(List<String[]> chunks) {
        for (String[] c : chunks) {
          switch (c[0]) {
            case "delta":
              bot.text.append(c[1]);
              bot.status = "answering…";
              break;
            case "status":
              bot.status = c[1];
              break;
            case "error":
              bot.error = c[1];
              break;
            case "meta":
              bot.latency = c[1];
              final String used = c[1].isEmpty() ? null : c[1].split("  ·  ")[0];
              if (used != null && !used.isEmpty()) {
                bot.model = used;
              }
              break;
            default:
              break;
          }
        }
        setStatus(bot.status);
        renderTranscript();
      }

      @Override
      protected void done() {
        bot.streaming = false;
        bot.status = null;
        if (answer.length() > 0) {
          history.add(new String[] {question, answer.toString()});
          while (history.size() > MAX_HISTORY_PAIRS) {
            history.remove(0);
          }
        }
        renderTranscript();
        setStatus(null);
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

  /** Flat, self-painted button (the Aqua L&F ignores setBackground). */
  static final class FlatButton extends JButton {
    private final Color bg;
    private final Color fg;
    private final Color outline;

    FlatButton(String text, Color bg, Color fg, Color outline) {
      super(text);
      this.bg = bg;
      this.fg = fg;
      this.outline = outline;
      setFont(new Font(UI_FONT, Font.BOLD, 12));
      setForeground(fg);
      setContentAreaFilled(false);
      setBorderPainted(false);
      setFocusPainted(false);
      setOpaque(false);
      setCursor(Cursor.getPredefinedCursor(Cursor.HAND_CURSOR));
      setMargin(new Insets(0, 0, 0, 0));
    }

    @Override
    public Dimension getPreferredSize() {
      final FontMetrics fm = getFontMetrics(getFont());
      return new Dimension(fm.stringWidth(getText()) + 28, fm.getHeight() + 14);
    }

    @Override
    protected void paintComponent(Graphics g) {
      final Graphics2D g2 = (Graphics2D) g.create();
      g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING,
                          RenderingHints.VALUE_ANTIALIAS_ON);
      g2.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING,
                          RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
      final int w = getWidth();
      final int hgt = getHeight();
      if (bg != null) {
        Color c = bg;
        if (!isEnabled()) {
          c = new Color(0x9AA8B5);
        }
        else if (getModel().isPressed()) {
          c = c.darker();
        }
        else if (getModel().isRollover()) {
          c = c.brighter();
        }
        g2.setColor(c);
        g2.fillRoundRect(0, 0, w, hgt, 4, 4);
      }
      if (outline != null) {
        g2.setColor(getModel().isRollover() ? outline.brighter() : outline);
        g2.drawRoundRect(0, 0, w - 1, hgt - 1, 4, 4);
      }
      g2.setColor(isEnabled() ? fg : new Color(0xEEF1EF));
      g2.setFont(getFont());
      final FontMetrics fm = g2.getFontMetrics();
      final int tx = (w - fm.stringWidth(getText())) / 2;
      final int ty = (hgt - fm.getHeight()) / 2 + fm.getAscent();
      g2.drawString(getText(), tx, ty);
      g2.dispose();
    }
  }

  /** Minimal Markdown -> HTML for the subset the server's answers use:
   *  paragraphs, **bold**, *italic*, `code`, headings, bullet/numbered
   *  lists, --- rules, pipe tables, links, fenced code. Output targets
   *  Swing's HTML 3.2 renderer (tables, font, simple CSS). */
  static final class Md {
    private Md() {}

    static String escape(String s) {
      final StringBuilder sb = new StringBuilder(s.length() + 16);
      for (int i = 0; i < s.length(); i++) {
        final char c = s.charAt(i);
        switch (c) {
          case '&': sb.append("&amp;"); break;
          case '<': sb.append("&lt;"); break;
          case '>': sb.append("&gt;"); break;
          case '"': sb.append("&quot;"); break;
          default: sb.append(c);
        }
      }
      return sb.toString();
    }

    static String toHtml(String md) {
      final String[] lines = md.replace("\r\n", "\n").split("\n", -1);
      final StringBuilder out = new StringBuilder();
      final StringBuilder para = new StringBuilder();
      String list = null;          // "ul" | "ol" | null
      boolean inCode = false;
      final List<String[]> table = new ArrayList<>();
      boolean tableHeaderDone = false;

      for (int idx = 0; idx <= lines.length; idx++) {
        final String raw = idx < lines.length ? lines[idx] : null;
        final String line = raw == null ? "" : raw;
        final String trimmed = line.trim();

        // fenced code
        if (raw != null && trimmed.startsWith("```")) {
          flushPara(out, para);
          list = closeList(out, list);
          tableHeaderDone = flushTable(out, table, tableHeaderDone);
          if (inCode) {
            out.append("</pre>");
          }
          else {
            out.append("<pre>");
          }
          inCode = !inCode;
          continue;
        }
        if (inCode) {
          out.append(escape(line)).append("\n");
          continue;
        }

        // pipe table rows
        if (raw != null && trimmed.startsWith("|") && trimmed.endsWith("|")
            && trimmed.length() > 1) {
          flushPara(out, para);
          list = closeList(out, list);
          final String inner = trimmed.substring(1, trimmed.length() - 1);
          if (inner.matches("[\\s:\\-|]+")) {
            tableHeaderDone = true;  // separator row: previous row is header
            continue;
          }
          table.add(inner.split("\\|", -1));
          continue;
        }
        else if (!table.isEmpty()) {
          tableHeaderDone = flushTable(out, table, tableHeaderDone);
        }

        if (raw == null || trimmed.isEmpty()) {
          flushPara(out, para);
          list = closeList(out, list);
          continue;
        }
        if (trimmed.matches("^(-{3,}|\\*{3,}|_{3,})$")) {
          flushPara(out, para);
          list = closeList(out, list);
          out.append("<div class='rule'></div>");
          continue;
        }
        if (trimmed.startsWith("#")) {
          flushPara(out, para);
          list = closeList(out, list);
          out.append("<h3>")
             .append(inline(trimmed.replaceFirst("^#+\\s*", "")))
             .append("</h3>");
          continue;
        }
        if (trimmed.matches("^[-*+]\\s+.*")) {
          flushPara(out, para);
          if (!"ul".equals(list)) {
            list = closeList(out, list);
            out.append("<ul>");
            list = "ul";
          }
          out.append("<li>").append(inline(trimmed.replaceFirst("^[-*+]\\s+", "")))
             .append("</li>");
          continue;
        }
        if (trimmed.matches("^\\d+[.)]\\s+.*")) {
          flushPara(out, para);
          if (!"ol".equals(list)) {
            list = closeList(out, list);
            out.append("<ol>");
            list = "ol";
          }
          out.append("<li>")
             .append(inline(trimmed.replaceFirst("^\\d+[.)]\\s+", "")))
             .append("</li>");
          continue;
        }
        if (trimmed.startsWith(">")) {
          flushPara(out, para);
          list = closeList(out, list);
          out.append("<p><i>").append(inline(trimmed.replaceFirst("^>\\s?", "")))
             .append("</i></p>");
          continue;
        }
        // continuation of a list item (indented text under a bullet)
        if (list != null && line.startsWith("  ")) {
          out.append(" ").append(inline(trimmed));
          continue;
        }
        list = closeList(out, list);
        if (para.length() > 0) {
          para.append(' ');
        }
        para.append(trimmed);
      }
      if (inCode) {
        out.append("</pre>");
      }
      return out.toString();
    }

    private static void flushPara(StringBuilder out, StringBuilder para) {
      if (para.length() > 0) {
        out.append("<p>").append(inline(para.toString())).append("</p>");
        para.setLength(0);
      }
    }

    private static String closeList(StringBuilder out, String list) {
      if (list != null) {
        out.append("</").append(list).append(">");
      }
      return null;
    }

    private static boolean flushTable(StringBuilder out, List<String[]> rows,
                                      boolean headerDone) {
      if (rows.isEmpty()) {
        return false;
      }
      out.append("<table class='md' cellspacing='0' cellpadding='0' "
                 + "border='0' width='100%'>");
      for (int r = 0; r < rows.size(); r++) {
        out.append("<tr>");
        final boolean th = r == 0 && headerDone;
        for (String cell : rows.get(r)) {
          out.append(th ? "<th>" : "<td>").append(inline(cell.trim()))
             .append(th ? "</th>" : "</td>");
        }
        out.append("</tr>");
      }
      out.append("</table><p></p>");
      rows.clear();
      return false;
    }

    /** Inline markup on one escaped line: code spans, bold, italic, links. */
    static String inline(String s) {
      // protect code spans first
      final List<String> codes = new ArrayList<>();
      final StringBuilder sb = new StringBuilder();
      int i = 0;
      while (i < s.length()) {
        final char c = s.charAt(i);
        if (c == '`') {
          final int j = s.indexOf('`', i + 1);
          if (j > i) {
            codes.add(escape(s.substring(i + 1, j)));
            sb.append("\u0000").append(codes.size() - 1).append("\u0000");
            i = j + 1;
            continue;
          }
        }
        sb.append(c);
        i++;
      }
      String t = escape(sb.toString());
      // links [text](url)
      t = t.replaceAll("\\[([^\\]]+)\\]\\((https?://[^)\\s]+)\\)",
                       "<a href='$2'>$1</a>");
      // bold / italic (bold first so ** isn't eaten by *)
      t = t.replaceAll("\\*\\*(.+?)\\*\\*", "<b>$1</b>");
      t = t.replaceAll("__(.+?)__", "<b>$1</b>");
      t = t.replaceAll("(?<![\\w*])\\*(?!\\s)(.+?)(?<!\\s)\\*(?![\\w*])", "<i>$1</i>");
      t = t.replaceAll("(?<![\\w_])_(?!\\s)(.+?)(?<!\\s)_(?![\\w_])", "<i>$1</i>");
      // restore code spans
      for (int k = 0; k < codes.size(); k++) {
        t = t.replace("\u0000" + k + "\u0000", "<code>" + codes.get(k) + "</code>");
      }
      return t;
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

  // --- Configurable plumbing (settings live in our own file, not here) --

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
