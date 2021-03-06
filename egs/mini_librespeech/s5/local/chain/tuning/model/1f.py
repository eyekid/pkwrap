#!/usr/bin/env python3
# %WER 21.70 [ 4369 / 20138, 385 ins, 957 del, 3027 sub ]

"""
    same as 1b but uses layernorm instead of
    batchnorm
"""

import argparse
import os
import torch
import torch.optim as optim
import torch.nn.functional as F
import torch.nn as nn
import torch.nn.init as init
import pkwrap
import numpy as np
from torch.nn.utils import clip_grad_value_
import sys

class TDNN(nn.Module):
    def __init__(self, feat_dim, output_dim, context_len=1, subsampling_factor=1):
        super(TDNN, self).__init__()
        self.linear = pkwrap.nn.NaturalAffineTransform(feat_dim*context_len, output_dim)
        self.output_dim = torch.tensor(output_dim, requires_grad=False)
        self.feat_dim = torch.tensor(feat_dim, requires_grad=False)
        self.subsampling_factor = torch.tensor(subsampling_factor, requires_grad=False)
        self.context_len = torch.tensor(context_len, requires_grad=False)

    def forward(self, input):
        mb, T, D = input.shape
        l = self.context_len
        N = T-l+1
        padded_input = torch.zeros(mb, N, D*self.context_len, device=input.device)
        start_d = 0
        for i in range(l):
            end_d = start_d + D
            padded_input[:,:,start_d:end_d] = input[:,i:i+N,:]
            start_d = end_d
        if self.subsampling_factor>1:
            padded_input = padded_input[:,::self.subsampling_factor,:]
        return self.linear(padded_input)

class TDNNBatchNorm(nn.Module):
    def __init__(self, feat_dim, output_dim, context_len=1, subsampling_factor=1):
        super(TDNNBatchNorm, self).__init__()
        self.tdnn = TDNN(feat_dim, output_dim, context_len, subsampling_factor)
        self.bn = nn.LayerNorm(output_dim)
        self.output_dim = torch.tensor(output_dim, requires_grad=False)

    def forward(self, input):
        mb, T, D = input.shape
        x = self.tdnn(input)
        x = self.bn(x)
        x = F.relu(x)
        return x

def train_lfmmi_one_iter(model, egs_file, den_fst_path, training_opts, feat_dim, 
    minibatch_size="64", use_gpu=True, lr=0.0001, weight_decay=0.25, frame_shift=0, print_interval=10):
    pkwrap.kaldi.InstantiateKaldiCuda()
    if training_opts is None:
        training_opts = pkwrap.kaldi.chain.CreateChainTrainingOptionsDefault()
    den_graph = pkwrap.kaldi.chain.LoadDenominatorGraph(den_fst_path, model.output_dim)
    criterion = pkwrap.chain.KaldiChainObjfFunction.apply
    if use_gpu:
        model = model.cuda()
    optimizer = optim.SGD(model.parameters(), lr=lr, weight_decay=weight_decay)
    acc_sum = torch.tensor(0., requires_grad=False)
    for mb_id, merged_egs in enumerate(pkwrap.chain.prepare_minibatch(egs_file, minibatch_size, 0)):
        features = pkwrap.kaldi.chain.GetFeaturesFromEgs(merged_egs)
        features = features[:,1+frame_shift:1+140+25+frame_shift,:]
        features = features.cuda()
        output, xent_output = model(features)
        sup = pkwrap.kaldi.chain.GetSupervisionFromEgs(merged_egs)
        deriv = criterion(training_opts, den_graph, sup, output, xent_output)
        acc_sum.add_(deriv[0])
        if mb_id>0 and mb_id%print_interval==0:
            sys.stderr.write("Overall objf={}\n".format(acc_sum/print_interval))
            acc_sum.zero_()
        optimizer.zero_grad()
        deriv.backward()
        clip_grad_value_(model.parameters(), 5.0)
        optimizer.step()
    sys.stdout.flush()
    model = model.cpu()
    return model

class Net(nn.Module):
    def __init__(self, output_dim, feat_dim):
        super(Net, self).__init__()
        self.input_dim = feat_dim
        self.output_dim = output_dim
        self.bn = nn.BatchNorm1d(feat_dim, affine=False)
        self.tdnn1 = TDNNBatchNorm(feat_dim, 512, context_len=5)
        self.tdnn2 = TDNNBatchNorm(512, 512, context_len=3)
        self.tdnn3 = TDNNBatchNorm(512, 512, context_len=3, subsampling_factor=3)
        self.tdnn4 = TDNNBatchNorm(512, 512, context_len=3)
        self.tdnn5 = TDNNBatchNorm(512, 512, context_len=3)
        self.tdnn6 = TDNNBatchNorm(512, 512, context_len=3)
        self.prefinal_chain = TDNNBatchNorm(512, 512, context_len=1)
        self.prefinal_xent = TDNNBatchNorm(512, 512, context_len=1)
        self.chain_output = pkwrap.nn.NaturalAffineTransform(512, output_dim)
        self.chain_output.weight.data.zero_()
        self.chain_output.bias.data.zero_()
        self.xent_output = pkwrap.nn.NaturalAffineTransform(512, output_dim)
        self.xent_output.weight.data.zero_()
        self.xent_output.bias.data.zero_()

    def forward(self, input): 
        mb, T, D = input.shape
        x = input.permute(0, 2, 1)
        x = self.bn(x)
        x = x.permute(0, 2, 1)
        x = self.tdnn1(input)
        x = self.tdnn2(x)
        x = self.tdnn3(x)
        x = self.tdnn4(x)
        x = self.tdnn5(x)
        x = self.tdnn6(x)
        chain_prefinal_out = self.prefinal_chain(x)
        xent_prefinal_out = self.prefinal_xent(x)
        chain_out = self.chain_output(chain_prefinal_out)
        xent_out = self.xent_output(xent_prefinal_out)
        return chain_out, F.log_softmax(xent_out, dim=2)

if __name__ == '__main__':
        parser = argparse.ArgumentParser(description="")
        parser.add_argument("--mode", default="init")
        parser.add_argument("--dir", default="")
        parser.add_argument("--lr", default=0.001, type=float)
        parser.add_argument("--egs", default="")
        parser.add_argument("--new-model", default="")
        parser.add_argument("--l2-regularize", default=1e-4, type=float)
        parser.add_argument("--l2-regularize-factor", default=1.0, type=float) # this is the weight_decay in pytorch
        parser.add_argument("--out-of-range-regularize", default=0.01, type=float)
        parser.add_argument("--xent-regularize", default=0.025, type=float)
        parser.add_argument("--leaky-hmm-coefficient", default=0.1, type=float)
        parser.add_argument("--minibatch-size", default="32", type=str)
        parser.add_argument("--decode-feats", default="data/test/feats.scp", type=str)
        parser.add_argument("--decode-output", default="-", type=str)
        parser.add_argument("--decode-iter", default="final", type=str)
        parser.add_argument("--frame-shift", default=0, type=int)
        parser.add_argument("base_model")

        args = parser.parse_args()
        dirname = args.dir
        num_outputs = None
        with open(os.path.join(dirname, "num_pdfs")) as ipf:
            num_outputs = int(ipf.readline().strip())
        assert num_outputs is not None
        feat_dim = None
        with open( os.path.join(dirname, "feat_dim")) as ipf:
            feat_dim = int(ipf.readline().strip())
        assert feat_dim is not None

        if args.mode == 'init':
            model = Net(num_outputs, feat_dim)
            torch.save(model.state_dict(), args.base_model)
        elif args.mode == 'training':
            lr = args.lr
            den_fst_path = os.path.join(dirname, "den.fst")

#           load model
            model = Net(num_outputs, feat_dim)
            base_model = args.base_model
            loader = torch.load(base_model)
            model.load_state_dict(torch.load(base_model))
            sys.stderr.write("Loaded base model from {}".format(base_model))

            training_opts = pkwrap.kaldi.chain.CreateChainTrainingOptions(args.l2_regularize, 
                                                                          args.out_of_range_regularize, 
                                                                          args.leaky_hmm_coefficient, 
                                                                          args.xent_regularize) 
            new_model = train_lfmmi_one_iter(
                            model,
                            args.egs, 
                            den_fst_path, 
                            training_opts, 
                            feat_dim, 
                            minibatch_size=args.minibatch_size, 
                            lr=args.lr,
                            weight_decay=args.l2_regularize_factor,
                            frame_shift=args.frame_shift)
            torch.save(new_model.state_dict(), args.new_model)
        elif args.mode == 'diagnostic':
            # TODO: implement diagnostics
            pass
        elif args.mode == 'merge':
            with torch.no_grad():
                base_models = args.base_model.split(',')
                assert len(base_models)>0
                model0 = Net(num_outputs, feat_dim)
                model0.load_state_dict(torch.load(base_models[0]))
                print(list(model0.parameters())[0])
                model_acc = dict(model0.named_parameters())
                for mdl_name in base_models[1:]:
                    this_mdl = Net(num_outputs, feat_dim)
                    this_mdl.load_state_dict(torch.load(mdl_name))
                    for name, params in this_mdl.named_parameters():
                        model_acc[name].data.add_(params.data)
                weight = 1.0/len(base_models)
                for name in model_acc:
                    model_acc[name].data.mul_(weight)
                print(list(model0.parameters())[0])
                torch.save(model0.state_dict(), args.new_model)

        elif args.mode == 'decode':
            with torch.no_grad():
                model = Net(num_outputs, feat_dim)
                base_model = args.base_model
                try:
                    model.load_state_dict(torch.load(base_model))
                except:
                    sys.stderr.write("Cannot load model {}".format(base_model))
                    quit(1)
                model.eval()
                writer_spec = "ark,t:{}".format(args.decode_output)
                writer = pkwrap.script_utils.feat_writer(writer_spec)
                for key, feats in pkwrap.script_utils.feat_reader_gen(args.decode_feats):
                    feats_with_context = pkwrap.matrix.add_context(feats, 13, 13).unsqueeze(0)
                    post, _ = model(feats_with_context)
                    post = post.squeeze(0)
                    writer.Write(key, pkwrap.kaldi.matrix.TensorToKaldiMatrix(post))
                    sys.stderr.write("Wrote {}\n ".format( key))
                    sys.stderr.flush()
                writer.Close()
                sys.stdout.flush()
