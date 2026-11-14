# 動作環境

- 動作環境はLinux及びWindows11とする。
- CLIの実行ファイルでShellから実行する。
- Pythonのインストールが必要ないFatExeファイルとする。
- FFMPEG.exe等の関連ファイルと同梱する。

# 機能

- ユーザはm3u8ファイルのURLを入力する。
- ユーザは動画ページのURL（`--from-page`）を入力し、ページからm3u8を自動検出できる。
- 検出はHTML・JavaScriptの静的解析と、Chrome / Edge / ChromiumをCDPで操作するブラウザ方式に対応する。
- m3u8の候補が複数ある場合は、一覧表示と番号指定（`--source-index`）で選択できる。
- RefererのURLを設定ファイルで指定可能とする。

- m3u8ファイルをダウンロードし内容を解析する。
- 記載されたファイル群をダウンロードする。
- ファイル群をffmegで結合し単一のmp4ファイルを作成する。結合方法は@list.cmdを参考とする。