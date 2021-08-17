import torch
import torch.nn as nn
import torch.nn.functional as F
from munch import munchify

from models.gpt_voice.lucidrains_gpt import Transformer
from models.tacotron2.taco_utils import get_mask_from_lengths
from models.tacotron2.text import symbols, sequence_to_text
from trainer.networks import register_model
from utils.util import opt_get


class ResBlock(nn.Module):
    def __init__(self, chan):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(chan, chan, kernel_size=5, padding = 2),
            nn.BatchNorm1d(chan),
            nn.ReLU(),
            nn.Conv1d(chan, chan, kernel_size=5, padding = 2),
            nn.BatchNorm1d(chan)
        )

    def forward(self, x):
        return F.relu(self.net(x) + x)


class MelEncoder(nn.Module):
    def __init__(self, channels, mel_channels=80):
        super().__init__()
        self.channels = channels
        self.encoder = nn.Sequential(nn.Conv1d(mel_channels, channels//4, kernel_size=7, padding=3),
                                     ResBlock(channels//4),
                                     ResBlock(channels//4),
                                     nn.Conv1d(channels//4, channels//2, kernel_size=5, stride=2, padding=2),
                                     nn.BatchNorm1d(channels//2),
                                     nn.ReLU(),
                                     ResBlock(channels//2),
                                     ResBlock(channels//2),
                                     ResBlock(channels//2),
                                     nn.Conv1d(channels//2, channels, kernel_size=5, stride=2, padding=2),
                                     ResBlock(channels),
                                     ResBlock(channels),
                                     ResBlock(channels)
                                     )

    def forward(self, x):
        return self.encoder(x)


class GptSegmentor(nn.Module):
    MAX_MEL_FRAMES = 2000 // 4

    def __init__(self, layers=8, model_dim=512, heads=8):
        super().__init__()

        self.model_dim = model_dim
        self.max_mel_frames = self.MAX_MEL_FRAMES
        self.mel_encoder = MelEncoder(model_dim)
        self.mel_pos_embedding = nn.Embedding(self.MAX_MEL_FRAMES, model_dim)
        self.gpt = Transformer(dim=model_dim, depth=layers, seq_len=self.MAX_MEL_FRAMES, heads=heads,
                               attn_dropout=.1, ff_dropout=.1, non_causal_sequence_partition=self.MAX_MEL_FRAMES)

        self.final_norm = nn.LayerNorm(model_dim)
        self.stop_head = nn.Linear(model_dim, 1)

    def forward(self, mel_inputs, termination_points=None):
        mel_emb = self.mel_encoder(mel_inputs)
        mel_emb = mel_emb.permute(0,2,1).contiguous()
        mel_emb = mel_emb + self.mel_pos_embedding(torch.arange(mel_emb.shape[1], device=mel_emb.device))

        enc = self.gpt(mel_emb)
        stop_logits = self.final_norm(enc)
        stop_logits = self.stop_head(stop_logits)

        if termination_points is not None:
            # The MEL gets decimated to 1/4 the size by the encoder, so we need to do the same to the termination points.
            termination_points = F.interpolate(termination_points.unsqueeze(1), size=mel_emb.shape[1], mode='area').squeeze()
            termination_points = (termination_points > 0).float()

            # Compute loss
            loss = F.binary_cross_entropy_with_logits(stop_logits.squeeze(-1), termination_points)
            return loss.mean()
        else:
            return stop_logits



@register_model
def register_gpt_segmentor(opt_net, opt):
    return GptSegmentor(**opt_get(opt_net, ['kwargs'], {}))


if __name__ == '__main__':
    gpt = GptSegmentor()
    l = gpt(torch.randn(3,80,94),
            torch.zeros(3,94))
    print(l.shape)

    #o = gpt.infer(torch.randint(high=24, size=(2,60)))
    #print(o.shape)


