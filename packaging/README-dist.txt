video-concat 使用手順
======================

Python のインストールは不要です。このフォルダ一式を任意の場所に置いて、
コマンドプロンプト / PowerShell / シェルから実行してください。

■ 必要なファイル

  video-concat.exe        プログラム本体（Linux では video-concat）
  ffmpeg.exe              ffmpeg（Linux では ffmpeg）
  video-concat.ini        設定ファイル
  _internal\              Python ランタイム（削除しないでください）
  LICENSES\               ffmpeg のライセンス情報

  ffmpeg.exe が無い場合は、初回起動時に自動ダウンロードします。
  自動ダウンロードできない環境では、ffmpeg.exe をこのフォルダに手動で置くか、
  --ffmpeg でパスを指定してください。

■ 基本的な使い方

  Windows（コマンドプロンプト / PowerShell）:

    video-concat.exe "https://example.com/video.m3u8"

  Linux:

    ./video-concat "https://example.com/video.m3u8"

  出力ファイル名を指定する場合:

    video-concat.exe "https://example.com/video.m3u8" -o output.mp4

  同時ダウンロード数を変更する場合:

    video-concat.exe "https://example.com/video.m3u8" -c 16

■ Referer の設定

  video-concat.ini の [http] セクションを編集します。

    [http]
    referer = https://example.com/

  Referer は m3u8 本体、マスターの子プレイリスト、すべてのセグメントに送られます。
  コマンドラインで上書きする場合:

    video-concat.exe "https://example.com/video.m3u8" -H "Referer: https://example.com/"

■ その他の設定

  video-concat.ini で同時接続数、タイムアウト、出力先、ffmpeg のパスなどを
  設定できます。各項目の説明は設定ファイル内のコメントを参照してください。

  コマンドライン引数は設定ファイルより優先されます。

■ 注意事項

  - このフォルダ全体を移動してください。video-concat.exe だけを移動すると
    必要なランタイムが無くなり、起動できません。
  - 初回起動時の ffmpeg 自動ダウンロードにはインターネット接続が必要です。
  - 書き込み権限のないフォルダ（Program Files など）では ffmpeg を自動配置
    できません。書き込み可能なフォルダに置くか、--ffmpeg を指定してください。
  - Windows の SmartScreen が警告を表示する場合があります。配布元を確認して
    実行してください。
  - ダウンロードする権利のあるコンテンツにのみ使用してください。

■ ライセンス

  ffmpeg は LGPL の静的ビルドを使用しています。
  詳細は LICENSES\ フォルダを参照してください。
