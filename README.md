# Local LLM GUI Release V1.0

Python製のネイティブGUIからOllamaを操作するローカルLLMクライアントです。

## 構成

```text
LocalLLMGUI/
├─ README.md
├─ start.cmd
├─ start.sh
├─ launcher.py
├─ requirements.txt
├─ config/
│  └─ gui_config.txt
├─ gui/
│  └─ app.py
├─ node/
│  ├─ server.js
│  └─ package.json
└─ data/
   └─ projects/
```

GUIはPython + Tkinter、Ollamaとの通信はNode.jsの薄いブリッジが担当します。HTML、WebView、Tauriは使用しません。

※補足：このリリースバージョンの前はJavaFXを使用したりするバージョン、htmlと自作の組み込んで使うブラウザを使用したりするバージョンが存在していました。それの表記でしたが、リリースバージョンは独立していることを忘れREADME.mdに記載しています。気にしなくても構いません。

## 起動

Windowsでは`start.cmd`をダブルクリックしてください。Linuxでは`./start.sh`を実行してください。

初回起動時は設定画面が表示されます。Ollamaの接続先を自動検出し、検出したモデルを既定モデル候補として表示します。接続先と既定モデルを確認して「設定して起動」を押すと、そのまま通常のGUIが起動します。

## Ollama自動検出

初回起動時に次のローカルポートを確認します。

```text
11434
11435
11436
11437
11438
11439
11440
```

`OLLAMA_HOST`環境変数が設定されている場合も先に確認します。

通常のOllamaは`127.0.0.1:11434`です。別ポートで動作していても検出できれば、その接続先を設定します。

## 設定

上部メニューバーの「設定」からOllama接続先と既定モデルを変更できます。

```text
設定
├─ Ollamaホスト
├─ Ollamaポート
└─ 既定モデル
```

保存時にはNode.jsブリッジへ新しい接続先が通知されるため、アプリを再起動せずに接続先を切り替えられます。

`config/gui_config.txt`は外部設定ファイルなので、基本設定を直接編集することもできます。

## モデル

GUIからOllamaモデルを追加できます。モデル名を入力すると`/api/pull`を利用して取得します。

モデル一覧はOllamaから自動取得します。

画像を添付した場合、選択中モデルがVision非対応なら、Ollamaに登録されているモデルからVision対応モデルを自動検索します。

## 添付ファイル

テキストファイルは内容を読み込み、UTF-8、UTF-8 BOM、UTF-16、CP932、EUC-JP、ISO-2022-JPを候補として自動判定します。典型的なUTF-8とCP932の取り違えによる日本語文字化けも可能な場合は復元します。

画像はPNG、JPEG、WebP、GIFに対応します。Vision対応モデルが必要です。

`tkinterdnd2`が利用可能な環境ではドラッグ＆ドロップ添付も使用できます。利用できない場合は「ファイル追加」から添付できます。

## Markdown

AIの回答はMarkdownとして表示します。見出し、太字、斜体、取り消し、箇条書き、番号付きリスト、引用、インラインコード、コードブロックなどを整形して表示します。

コードブロックは専用領域として表示され、「コピー」ボタンからコードだけをクリップボードへコピーできます。Markdownの装飾記号は通常表示されません。

## プロジェクトと会話

会話はプロジェクト単位で保存されます。

```text
data/projects/
└─ project_id/
   ├─ project.json
   └─ conversations/
      └─ conversation.json
```

プロジェクトの作成、名前変更、切り替え、フォルダ表示に対応しています。会話は名前変更、削除、JSON/Markdownエクスポートに対応しています。

## メニューバー

```text
ファイル
編集
表示
プロジェクト
設定
ヘルプ
```

設定は「設定」メニューから開けます。

## キーボード操作

```text
Ctrl + Enter       送信
Ctrl + Shift + V   ファイル追加
```

## Node.js Bridge

Node.js側は外部npmパッケージを使用しません。Node.js本体があれば動作します。

主なエンドポイントは次の通りです。

```text
GET  /health
GET  /models
POST /set-ollama
POST /chat
POST /stop
POST /pull
POST /vision-model
```

## 必要環境

```text
Python 3.9以降
Node.js
Ollama
```

画像を扱う場合はVision対応モデルも必要です。

## リリース方針

Release V1.0では、通常利用に必要なGUI機能とOllama接続設定をまとめています。Pythonソース、Node.jsブリッジ、設定ファイル、会話データを分離しているため、実行時に設定を変更できます。

リリース版のソースコードには開発用コメントを残さず、READMEに必要な仕様を記録しています。

## バージョン

**Local LLM GUI Release V1.0**
