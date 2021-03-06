// Copyright (c) 2020 Idiap Research Institute, http://www.idiap.ch/
// Written by Srikanth Madikeri <srikanth.madikeri@idiap.ch>

#ifndef PKWRAP_MATRIX_H_
#define PKWRAP_MATRIX_H_ 
#include "common.h"
#include "base/kaldi-common.h"
#include "util/common-utils.h"
#include "matrix/kaldi-matrix.h"
#include "transform/transform-common.h"

torch::Tensor KaldiMatrixToTensor(kaldi::Matrix<kaldi::BaseFloat> &mat);
torch::Tensor KaldiCudaMatrixToTensor(kaldi::CuMatrix<kaldi::BaseFloat>& kmat);
torch::Tensor KaldiCudaVectorToTensor(const kaldi::CuVector<kaldi::BaseFloat>& kvec);
kaldi::CuSubMatrix<kaldi::BaseFloat> TensorToKaldiCuSubMatrix(torch::Tensor &t);
kaldi::CuSubVector<kaldi::BaseFloat> TensorToKaldiCuSubVector(torch::Tensor &t);
torch::Tensor ReadKaldiMatrixFile(const std::string &file_name);
kaldi::Matrix<kaldi::BaseFloat> TensorToKaldiMatrix(torch::Tensor &t);
#endif
