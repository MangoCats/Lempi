package io.github.mangocats.lempi

import android.os.Looper
import androidx.annotation.OptIn
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.SimpleBasePlayer
import androidx.media3.common.util.UnstableApi
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import io.github.mangocats.lempi.ffi.NowPlaying

/**
 * The phone's controls, reaching the player [REQ-AND-130], [GDE-APP-100]: a
 * Media3 player that plays nothing. It describes what Lempi's own player is
 * doing -- playing or not, and what -- so the media session can show it on
 * the lock screen, in the notification, over Bluetooth and in Android Auto;
 * and it turns what those send back into the player's own commands. The audio
 * stays in the Rust engine, which Media3 never touches.
 *
 * Lempi is a radio, not a playlist: there is no previous and no seeking, only
 * play, pause and skip. Skip is offered as "next", which needs a next item to
 * be offered at all, so the timeline holds two: what is playing, and the next
 * one to come. Asking for the second is a skip.
 *
 * State is pushed in by [PlayerService] from the player's own snapshot, once a
 * second; Media3 asks [getState] after each [publish].
 */
@OptIn(UnstableApi::class)
class LempiPlayer(
    private val onPlay: () -> Unit,
    private val onPause: () -> Unit,
    private val onSkip: () -> Unit,
) : SimpleBasePlayer(Looper.getMainLooper()) {
    private var now: NowPlaying? = null

    /** What the engine last published, or null when it is not running. */
    fun publish(n: NowPlaying?) {
        now = n
        invalidateState()
    }

    override fun getState(): State {
        val commands = Player.Commands.Builder().addAll(
            Player.COMMAND_PLAY_PAUSE,
            Player.COMMAND_SEEK_TO_NEXT,
            Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM,
            Player.COMMAND_GET_CURRENT_MEDIA_ITEM,
            Player.COMMAND_GET_METADATA,
            Player.COMMAND_GET_TIMELINE,
            Player.COMMAND_SET_MEDIA_ITEM,
            Player.COMMAND_PREPARE,
        ).build()
        val n = now
        val b = State.Builder()
            .setAvailableCommands(commands)
            .setPlayWhenReady(n?.playing == true, Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST)
            .setPlaybackState(if (n == null) Player.STATE_IDLE else Player.STATE_READY)
        if (n != null) {
            val playing = MediaMetadata.Builder()
                .setTitle(n.title ?: "Lempi")
                .setArtist(n.artist)
                .setIsPlayable(true)
                .setIsBrowsable(false)
                .build()
            val next = MediaMetadata.Builder().setTitle("Next").setIsPlayable(true).setIsBrowsable(false).build()
            b.setPlaylist(listOf(
                MediaItemData.Builder(NOW).setMediaItem(
                    MediaItem.Builder().setMediaId(NOW).setMediaMetadata(playing).build()).build(),
                MediaItemData.Builder(NEXT).setMediaItem(
                    MediaItem.Builder().setMediaId(NEXT).setMediaMetadata(next).build()).build(),
            ))
                .setCurrentMediaItemIndex(0)
                .setContentPositionMs(n.positionMs.toLong())
        }
        return b.build()
    }

    override fun handleSetPlayWhenReady(playWhenReady: Boolean): ListenableFuture<*> {
        if (playWhenReady) onPlay() else onPause()
        return Futures.immediateVoidFuture()
    }

    override fun handleSeek(mediaItemIndex: Int, positionMs: Long, seekCommand: Int): ListenableFuture<*> {
        if (seekCommand == Player.COMMAND_SEEK_TO_NEXT || seekCommand == Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM ||
            mediaItemIndex == 1) {
            onSkip()
        }
        return Futures.immediateVoidFuture()
    }

    /** Android Auto choosing "Lempi radio": it plays. */
    override fun handleSetMediaItems(
        mediaItems: MutableList<MediaItem>, startIndex: Int, startPositionMs: Long,
    ): ListenableFuture<*> {
        onPlay()
        return Futures.immediateVoidFuture()
    }

    override fun handlePrepare(): ListenableFuture<*> = Futures.immediateVoidFuture()

    companion object {
        private const val NOW = "now"
        private const val NEXT = "next"
    }
}
