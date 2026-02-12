#!/usr/bin/env bash
# FAST VERSION: Uses yt-dlp + Aria2 instead of FFmpeg

set -e
set -u

usage() {
    printf "%b\n" "$(grep '^#/' "$0" | cut -c4-)" && exit 1
}

set_var() {
    _CURL="$(command -v curl)" || command_not_found "curl"
    _JQ="$(command -v jq)" || command_not_found "jq"
    if [[ -z ${ANIMEPAHE_DL_NODE:-} ]]; then
        _NODE="$(command -v node)" || command_not_found "node"
    else
        _NODE="$ANIMEPAHE_DL_NODE"
    fi
    _YTDLP="$(command -v yt-dlp)" || command_not_found "yt-dlp"

    _HOST="https://animepahe.si"
    _ANIME_URL="$_HOST/anime"
    _API_URL="$_HOST/api"
    _REFERER_URL="https://kwik.cx/"
    _SCRIPT_PATH=$(dirname "$(realpath "$0")")
    _ANIME_LIST_FILE="$_SCRIPT_PATH/anime.list"
    _SOURCE_FILE=".source.json"
}

set_args() {
    _PARALLEL_JOBS=1
    while getopts ":hlda:s:e:r:t:o:" opt; do
        case $opt in
            a) _INPUT_ANIME_NAME="$OPTARG" ;;
            s) _ANIME_SLUG="$OPTARG" ;;
            e) _ANIME_EPISODE="$OPTARG" ;;
            l) _LIST_LINK_ONLY=true ;;
            r) _ANIME_RESOLUTION="$OPTARG" ;;
            t) _PARALLEL_JOBS="$OPTARG" ;;
            o) _ANIME_AUDIO="$OPTARG" ;;
            d) _DEBUG_MODE=true; set -x ;;
            h) usage ;;
            \?) print_error "Invalid option: -$OPTARG" ;;
        esac
    done
}

print_error() { printf "%b\n" "\033[31m[ERROR]\033[0m $1" >&2; exit 1; }
print_warn() { printf "%b\n" "\033[33m[WARNING]\033[0m $1" >&2; }
print_info() { printf "%b\n" "\033[32m[INFO]\033[0m $1" >&2; }
command_not_found() { print_error "$1 command not found!"; }

get() {
    "$_CURL" -sS -L "$1" \
        -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
        -H "cookie: $_COOKIE" --compressed
}

set_cookie() {
    local u; u="$(LC_ALL=C tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 16)"
    _COOKIE="__ddg2_=$u"
}

download_anime_list() {
    get "$_ANIME_URL" | grep "/anime/" | sed -E 's/.*anime\//[/;s/" title="/] /;s/\">.*/   /;s/" title/]/' > "$_ANIME_LIST_FILE"
}

search_anime_by_name() {
    local d n
    d="$(get "$_HOST/api?m=search&q=${1// /%20}")"
    n="$("$_JQ" -r '.total' <<< "$d")"
    if [[ "$n" -eq "0" ]]; then echo ""; else "$_JQ" -r '.data[] | "[\(.session)] \(.title)   "' <<< "$d" | tee -a "$_ANIME_LIST_FILE" | remove_slug; fi
}

get_episode_list() { get "${_API_URL}?m=release&id=${1}&sort=episode_asc&page=${2}"; }

download_source() {
    local d p n
    mkdir -p "$_SCRIPT_PATH/$_ANIME_NAME"
    d="$(get_episode_list "$_ANIME_SLUG" "1")"
    p="$("$_JQ" -r '.last_page' <<< "$d")"
    if [[ "$p" -gt "1" ]]; then
        for i in $(seq 2 "$p"); do
            n="$(get_episode_list "$_ANIME_SLUG" "$i")"
            d="$(echo "$d $n" | "$_JQ" -s '.[0].data + .[1].data | {data: .}')"
        done
    fi
    echo "$d" > "$_SCRIPT_PATH/$_ANIME_NAME/$_SOURCE_FILE"
}

get_episode_link() {
    local s o l r=""
    s=$("$_JQ" -r '.data[] | select((.episode | tonumber) == ($num | tonumber)) | .session' --arg num "$1" < "$_SCRIPT_PATH/$_ANIME_NAME/$_SOURCE_FILE")
    [[ "$s" == "" ]] && print_warn "Episode $1 not found!" && return
    o="$("$_CURL" --compressed -sSL -H "cookie: $_COOKIE" "${_HOST}/play/${_ANIME_SLUG}/${s}")"
    l="$(grep \<button <<< "$o" | grep data-src | sed -E 's/data-src="/\n/g' | grep 'data-av1="0"')"
    if [[ -n "${_ANIME_AUDIO:-}" ]]; then r="$(grep 'data-audio="'"$_ANIME_AUDIO"'"' <<< "$l")"; fi
    if [[ -n "${_ANIME_RESOLUTION:-}" ]]; then r="$(grep 'data-resolution="'"$_ANIME_RESOLUTION"'"' <<< "${r:-$l}")"; fi
    if [[ -z "${r:-}" ]]; then grep kwik <<< "$l" | tail -1 | grep kwik | awk -F '"' '{print $1}'; else awk -F '" ' '{print $1}' <<< "$r" | tail -1; fi
}

get_playlist_link() {
    local s l
    s="$("$_CURL" --compressed -sS -H "Referer: $_REFERER_URL" -H "cookie: $_COOKIE" "$1" | grep "<script>eval(" | awk -F 'script>' '{print $2}'| sed -E 's/document/process/g' | sed -E 's/querySelector/exit/g' | sed -E 's/eval\(/console.log\(/g')"
    l="$("$_NODE" -e "$s" | grep 'source=' | sed -E "s/.m3u8';.*/.m3u8/" | sed -E "s/.*const source='//")"
    echo "$l"
}

download_episode() {
    local num="$1" l pl v
    v="$_SCRIPT_PATH/${_ANIME_NAME}/${_ANIME_NAME} - Episode ${num}.mp4"
    l=$(get_episode_link "$num")
    [[ "$l" != *"/"* ]] && print_warn "Link error!" && return
    pl=$(get_playlist_link "$l")
    [[ -z "${pl:-}" ]] && print_warn "Playlist error!" && return

    print_info "Downloading Episode $1 (High Speed)..."
    "$_YTDLP" --referer "$_REFERER_URL" "$pl" -o "$v" --no-part
}

download_episodes() {
    local origel el uniqel
    origel=()
    if [[ "$1" == *","* ]]; then IFS="," read -ra ADDR <<< "$1"; for n in "${ADDR[@]}"; do origel+=("$n"); done; else origel+=("$1"); fi
    el=()
    for i in "${origel[@]}"; do
        if [[ "$i" == *"*"* ]]; then
            local eps fst lst;
            eps="$("$_JQ" -r '.data[].episode' "$_SCRIPT_PATH/$_ANIME_NAME/$_SOURCE_FILE" | sort -nu)"; fst="$(head -1 <<< "$eps")"; lst="$(tail -1 <<< "$eps")";
            i="${fst}-${lst}"
        fi
        if [[ "$i" == *"-"* ]]; then s=$(awk -F '-' '{print $1}';); e=$(awk -F '-' '{print $2}'); for n in $(seq "$s" "$e"); do el+=("$n"); done; else el+=("$i"); fi
    done
    IFS=" " read -ra uniqel <<< "$(printf '%s\n' "${el[@]}" | sort -n -u | tr '\n' ' ')"

    local total_count=${#uniqel[@]}
    local current_count=0

    for e in "${uniqel[@]}"; do
        current_count=$((current_count+1))
        echo "BATCH_PROGRESS=$current_count/$total_count"
        download_episode "$e"
    done
}

remove_brackets() { awk -F']' '{print $1}' | sed -E 's/^\[//'; }
remove_slug() { awk -F'] ' '{print $2}'; }
get_slug_from_name() { grep "] $1" "$_ANIME_LIST_FILE" | tail -1 | remove_brackets; }

main() {
    set_args "$@"
    set_var
    set_cookie
    if [[ -n "${_INPUT_ANIME_NAME:-}" ]]; then
        search_res=$(search_anime_by_name "$_INPUT_ANIME_NAME")
        [[ -z "$search_res" ]] && print_error "Anime not found"
        _ANIME_NAME=$(head -n 1 <<< "$search_res")
        _ANIME_SLUG="$(get_slug_from_name "$_ANIME_NAME")"
    else
        download_anime_list
        [[ -z "${_ANIME_SLUG:-}" ]] && print_error "Slug required."
    fi
    [[ "$_ANIME_SLUG" == "" ]] && print_error "Slug not found!"
    _ANIME_NAME_CLEAN="$(grep "$_ANIME_SLUG" "$_ANIME_LIST_FILE" | tail -1 | remove_slug | sed -E 's/[[:space:]]+$//' | sed -E 's/[^[:alnum:] ,\+\-\)\(]/_/g')"
    if [[ -z "$_ANIME_NAME_CLEAN" ]]; then _ANIME_NAME_CLEAN="anime_$_ANIME_SLUG"; fi
    _ANIME_NAME="$_ANIME_NAME_CLEAN"
    download_source
    [[ -z "${_ANIME_EPISODE:-}" ]] && print_error "Episode required."
    download_episodes "$_ANIME_EPISODE"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
